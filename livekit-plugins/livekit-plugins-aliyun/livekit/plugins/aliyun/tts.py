from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterable, Optional

import aiohttp
from osc_data.text_stream import TextStreamSentencizer

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
    utils,
)

from .log import logger

DEFAULT_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

# how long to wait for `session.finished` after sending `session.finish`
_SESSION_FINISH_TIMEOUT = 5.0


def _new_event_id() -> str:
    return f"event_{int(time.time() * 1000)}"


def make_event(event_type: str, **payload: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"event_id": _new_event_id(), "type": event_type}
    event.update(payload)
    return event


def make_append_event(text: str) -> dict[str, Any]:
    return make_event("input_text_buffer.append", text=text)


def make_commit_event() -> dict[str, Any]:
    return make_event("input_text_buffer.commit")


def make_session_finish_event() -> dict[str, Any]:
    return make_event("session.finish")


def parse_server_event(event: dict[str, Any]) -> tuple[str, bytes | None]:
    event_type = str(event.get("type") or "")
    if event_type == "error":
        err = event.get("error") or {}
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise APIStatusError(
            message=str(message or "aliyun qwen tts error"),
            body=event,
            retryable=False,
        )
    if event_type == "response.audio.delta":
        delta = event.get("delta") or ""
        try:
            return event_type, base64.b64decode(delta, validate=True)
        except Exception as exc:
            raise APIStatusError(
                message="aliyun qwen tts invalid base64 audio delta",
                body=event,
                retryable=False,
            ) from exc
    return event_type, None


@dataclass
class TTSOptions:
    api_key: str
    model: str
    voice: str
    sample_rate: int
    language_type: str
    base_url: str = DEFAULT_BASE_URL

    def get_ws_url(self) -> str:
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}model={self.model}"

    def get_ws_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def get_session_update_event(self) -> dict[str, Any]:
        return make_event(
            "session.update",
            session={
                "mode": "commit",
                "voice": self.voice,
                "response_format": "pcm",
                "sample_rate": self.sample_rate,
                "language_type": self.language_type,
            },
        )


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "qwen3-tts-flash-realtime",
        voice: str = "Cherry",
        sample_rate: int = 24000,
        language_type: str = "Auto",
        base_url: str = DEFAULT_BASE_URL,
        http_session: aiohttp.ClientSession | None = None,
        max_session_duration: float = 600,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY must be set")
        self._session = http_session
        self._opts = TTSOptions(
            api_key=api_key,
            model=model,
            voice=voice,
            sample_rate=sample_rate,
            language_type=language_type,
            base_url=base_url,
        )
        self._pool = utils.ConnectionPool[aiohttp.ClientWebSocketResponse](
            connect_cb=self._connect_ws,
            close_cb=self._close_ws,
            max_session_duration=max_session_duration,
            mark_refreshed_on_get=True,
        )

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()

        return self._session

    async def _connect_ws(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        session = self._ensure_session()
        url = self._opts.get_ws_url()
        headers = self._opts.get_ws_header()
        try:
            return await asyncio.wait_for(
                session.ws_connect(url, headers=headers),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise APITimeoutError() from exc
        except aiohttp.ClientResponseError as exc:
            # covers aiohttp.WSServerHandshakeError, a subclass of this
            raise APIConnectionError(
                message=(
                    "aliyun qwen tts websocket handshake failed "
                    f"(status={exc.status})"
                )
            ) from exc
        except aiohttp.ClientError as exc:
            raise APIConnectionError(
                message="aliyun qwen tts websocket connection failed"
            ) from exc

    async def _close_ws(self, ws: aiohttp.ClientWebSocketResponse):
        await ws.close()

    def synthesize(
        self,
        text: str,
    ) -> AsyncIterable[tts.SynthesizedAudio]:
        raise NotImplementedError

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "SynthesizeStream":
        return SynthesizeStream(tts=self, opts=self._opts, conn_options=conn_options)


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(
        self,
        *,
        tts: TTS,
        opts: TTSOptions,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ):
        super().__init__(tts=tts, conn_options=conn_options)
        self._opts = opts
        self._response_done: asyncio.Future[None] | None = None
        self._session_finished: asyncio.Future[None] | None = None
        self._recv_error: BaseException | None = None
        self._sentence_start_time: float | None = None
        self._sentence_first_response_logged = False

    def _fail_pending(self, exc: BaseException) -> None:
        if self._recv_error is None:
            self._recv_error = exc
        if self._response_done is not None and not self._response_done.done():
            self._response_done.set_exception(exc)
        if self._session_finished is not None and not self._session_finished.done():
            self._session_finished.set_exception(exc)

    async def _recv_loop(
        self, ws: aiohttp.ClientWebSocketResponse, emitter: tts.AudioEmitter
    ) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        event = json.loads(msg.data)
                    except json.JSONDecodeError:
                        self._fail_pending(
                            APIStatusError(
                                message="aliyun qwen tts returned invalid json payload",
                                body=msg.data,
                                retryable=False,
                            )
                        )
                        return

                    try:
                        event_type, chunk = parse_server_event(event)
                    except APIStatusError as exc:
                        self._fail_pending(exc)
                        return

                    if chunk is not None:
                        if (
                            self._sentence_start_time is not None
                            and not self._sentence_first_response_logged
                        ):
                            self._sentence_first_response_logged = True
                            spent = time.perf_counter() - self._sentence_start_time
                            logger.info(
                                "tts first response",
                                extra={"spent": round(spent, 4)},
                            )
                        emitter.push(chunk)

                    if event_type == "response.done":
                        if (
                            self._response_done is not None
                            and not self._response_done.done()
                        ):
                            self._response_done.set_result(None)
                    elif event_type == "session.finished":
                        if (
                            self._session_finished is not None
                            and not self._session_finished.done()
                        ):
                            self._session_finished.set_result(None)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self._fail_pending(
                        APIConnectionError(
                            message="aliyun qwen tts websocket error"
                        )
                    )
                    return
        except aiohttp.ClientError as exc:
            conn_err = APIConnectionError(
                message="aliyun qwen tts connection failed"
            )
            conn_err.__cause__ = exc
            self._fail_pending(conn_err)
        finally:
            # covers the CLOSE/CLOSING/CLOSED break above as well as any
            # natural/unexpected exit of the loop; _fail_pending() is a
            # no-op for futures that are already done, so this will not
            # clobber a result/error that was already delivered.
            self._fail_pending(
                APIConnectionError(message="aliyun qwen tts websocket closed")
            )

    async def _run(self, emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
            frame_size_ms=200,
        )

        tts_inst: TTS = self._tts
        sentencizer = TextStreamSentencizer(remove_emoji=True)

        self._response_done = None
        self._session_finished = None
        self._recv_error = None
        self._sentence_start_time = None
        self._sentence_first_response_logged = False

        is_first_sentence = True
        stream_start_time = time.perf_counter()

        async with tts_inst._pool.connection(timeout=self._conn_options.timeout) as ws:
            recv_task = asyncio.create_task(self._recv_loop(ws, emitter))
            try:
                try:
                    await ws.send_json(self._opts.get_session_update_event())
                except aiohttp.ClientError as exc:
                    raise APIConnectionError(
                        message="aliyun qwen tts connection failed"
                    ) from exc

                async for token in self._input_ch:
                    if isinstance(token, self._FlushSentinel):
                        sentences = sentencizer.flush()
                    else:
                        sentences = sentencizer.push(text=token)

                    for sentence in sentences:
                        if len(sentence.strip()) == 0:
                            continue

                        if self._recv_error is not None:
                            raise self._recv_error

                        if is_first_sentence:
                            is_first_sentence = False
                            elapsed = time.perf_counter() - stream_start_time
                            logger.info(
                                "llm first sentence",
                                extra={"spent": round(elapsed, 4)},
                            )

                        emitter.start_segment(segment_id=utils.shortuuid())
                        logger.info("tts start", extra={"sentence": sentence})
                        self._sentence_start_time = time.perf_counter()
                        self._sentence_first_response_logged = False
                        self._response_done = asyncio.Future()

                        try:
                            await ws.send_json(make_append_event(sentence))
                            await ws.send_json(make_commit_event())
                            await asyncio.wait_for(
                                self._response_done,
                                timeout=self._conn_options.timeout,
                            )
                        except asyncio.TimeoutError as exc:
                            raise APITimeoutError() from exc
                        except aiohttp.ClientError as exc:
                            raise APIConnectionError(
                                message="aliyun qwen tts connection failed"
                            ) from exc

                        emitter.end_segment()
                        logger.info(
                            "tts end",
                            extra={
                                "spent": round(
                                    time.perf_counter() - self._sentence_start_time,
                                    4,
                                )
                            },
                        )
                        self._pushed_text = self._pushed_text.replace(sentence, "")

                if self._recv_error is not None:
                    raise self._recv_error

                self._session_finished = asyncio.Future()
                try:
                    await ws.send_json(make_session_finish_event())
                    await asyncio.wait_for(
                        self._session_finished, timeout=_SESSION_FINISH_TIMEOUT
                    )
                except (
                    asyncio.TimeoutError,
                    aiohttp.ClientError,
                    APIStatusError,
                    APIConnectionError,
                ) as exc:
                    # all audio for this stream was already delivered successfully;
                    # a failed/absent session.finish handshake just means we close
                    # the (now unusable) connection instead of returning it to the
                    # pool.
                    logger.warning(
                        "aliyun qwen tts session.finish handshake failed",
                        extra={"error": str(exc)},
                    )
            finally:
                await utils.aio.gracefully_cancel(recv_task)
                tts_inst._pool.remove(ws)

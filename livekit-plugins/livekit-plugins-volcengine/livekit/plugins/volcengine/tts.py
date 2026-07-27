from __future__ import annotations

import asyncio
import base64
import gzip
import json
import os
import time
import weakref
from collections.abc import ByteString
from typing import Literal, Tuple

import aiohttp
from pydantic import BaseModel, Field
from osc_data.text_stream import TextStreamSentencizer

from livekit.agents import (
    APIConnectOptions,
    APIStatusError,
    tts,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from .log import logger


def infer_resource_id(voice: str) -> str:
    if voice.startswith("S_"):
        return "seed-icl-2.0"
    if voice == "custom_mix_bigtts":
        return "seed-tts-1.0"
    if voice.startswith("ICL_") and voice.endswith("_tob"):
        return "seed-tts-1.0"
    if voice.startswith("saturn_") and voice.endswith("_tob"):
        return "seed-tts-2.0"
    if voice.endswith("_uranus_bigtts"):
        return "seed-tts-2.0"
    if (
        voice.endswith("_mars_bigtts")
        or voice.endswith("_moon_bigtts")
        or voice.endswith("_emo_v2_mars_bigtts")
        or voice.endswith("_conversation_wvae_bigtts")
    ):
        return "seed-tts-1.0"
    return "seed-tts-2.0"


SMALL_TTS_CLUSTER = "volcano_tts"
SMALL_TTS_WS_BASE_URL = "wss://openspeech.bytedance.com/api/v1"


def infer_fallback_voice(voice: str) -> str:
    parts = set(voice.lower().split("_"))
    if "female" in parts:
        return "BV001_streaming"
    if "male" in parts:
        return "BV002_streaming"
    raise ValueError(f"cannot infer gender from voice: {voice}")


def is_concurrency_quota_error(message: str) -> bool:
    lower = message.lower()
    return "quota exceeded" in lower and "concurrency" in lower


class ConcurrentQuotaExceededError(APIStatusError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message)


class _TTSOptions(BaseModel):
    app_id: str
    access_token: str | None = None
    voice: str = "zh_female_xiaohe_uranus_bigtts"
    base_url: str = "https://openspeech.bytedance.com"
    sample_rate: Literal[24000, 16000, 8000] = 24000
    encoding: Literal["mp3", "pcm"] = "pcm"
    speed: float = Field(1.0, ge=0.2, le=3.0)
    volume: float = Field(1.0, gt=0.1, le=3.0)
    pitch: float = Field(1.0, ge=0.1, le=3.0)

    def get_http_url(self) -> str:
        return f"{self.base_url}/api/v3/tts/unidirectional"

    def get_http_request(
        self, text: str, *, reqid: str | None = None, uid: str | None = None
    ) -> dict:
        if uid is None:
            uid = utils.shortuuid()
        if reqid is None:
            reqid = utils.shortuuid()
        return {
            "user": {"uid": uid},
            "req_params": {
                "text": text,
                "speaker": self.voice,
                "audio_params": {
                    "format": self.encoding,
                    "sample_rate": self.sample_rate,
                },
                "request_id": reqid,
            },
        }

    def get_http_header(self, reqid: str | None = None) -> dict[str, str]:
        if self.access_token is None:
            self.access_token = os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN")
            if self.access_token is None:
                raise ValueError("VOLCENGINE_TTS_ACCESS_TOKEN is not set")
        resource_id = infer_resource_id(self.voice)
        headers = {
            "Content-Type": "application/json",
            "X-Api-App-Id": self.app_id,
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": resource_id,
        }
        if reqid is not None:
            headers["X-Api-Request-Id"] = reqid
        return headers

    def get_ws_url(self) -> str:
        return f"{SMALL_TTS_WS_BASE_URL}/tts/ws_binary"

    def get_ws_header(self) -> dict[str, str]:
        if self.access_token is None:
            self.access_token = os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN")
            if self.access_token is None:
                raise ValueError("VOLCENGINE_TTS_ACCESS_TOKEN is not set")
        return {"Authorization": f"Bearer;{self.access_token}"}

    def get_ws_query_params(
        self, text: str, *, voice: str, uid: str | None = None
    ) -> bytearray:
        if uid is None:
            uid = utils.shortuuid()
        if self.access_token is None:
            self.access_token = os.getenv("VOLCENGINE_TTS_ACCESS_TOKEN")
            if self.access_token is None:
                raise ValueError("VOLCENGINE_TTS_ACCESS_TOKEN is not set")
        submit_request_json = {
            "app": {
                "appid": self.app_id,
                "token": self.access_token,
                "cluster": SMALL_TTS_CLUSTER,
            },
            "user": {"uid": uid},
            "audio": {
                "voice_type": voice,
                "encoding": self.encoding,
                "speed_ratio": self.speed,
                "volume_ratio": self.volume,
                "pitch_ratio": self.pitch,
                "rate": self.sample_rate,
            },
            "request": {
                "reqid": utils.shortuuid(),
                "text": text,
                "text_type": "plain",
                "operation": "submit",
                "with_frontend": 1,
                "frontend_type": "unitTson",
            },
        }
        default_header = bytearray(b"\x11\x10\x11\x00")
        payload_bytes = gzip.compress(json.dumps(submit_request_json).encode())
        full_client_request = bytearray(default_header)
        full_client_request.extend(len(payload_bytes).to_bytes(4, "big"))
        full_client_request.extend(payload_bytes)
        return full_client_request


class TTS(tts.TTS):
    def __init__(
        self,
        app_id: str,
        access_token: str | None = None,
        voice: str = "zh_female_xiaohe_uranus_bigtts",
        speed: float = 1.0,
        volume: float = 1.0,
        pitch: float = 1.0,
        sample_rate: Literal[24000, 16000, 8000] = 16000,
        http_session: aiohttp.ClientSession | None = None,
    ):
        """VolcEngine TTS

        Args:
            app_id (str): the app id of the tts, you can get it from the console.
            access_token (str | None, optional): the access token of the tts, if not provided, the value of the environment variable VOLCENGINE_TTS_ACCESS_TOKEN will be used. Defaults to None.
            voice (str, optional): the voice id used by the tts request. `resource_id` is inferred automatically from the voice (e.g. `*_uranus_bigtts` -> `seed-tts-2.0`, `*_mars_bigtts` -> `seed-tts-1.0`, `S_*` -> `seed-icl-2.0`). Defaults to `zh_female_xiaohe_uranus_bigtts`.
            sample_rate (Literal[24000, 16000, 8000], optional): the sample rate of the tts. Defaults to 24000.
            streaming (bool, optional): whether to use the streaming api. Defaults to True.
            http_session (aiohttp.ClientSession | None, optional): the http session to use. Defaults to None.
        """
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._opts = _TTSOptions(
            app_id=app_id,
            access_token=access_token,
            voice=voice,
            sample_rate=sample_rate,
            speed=speed,
            volume=volume,
            pitch=pitch,
        )
        self._session = http_session
        self._streams = weakref.WeakSet[SynthesizeStream]()
        self._use_small_tts = False
        self._small_pool: (
            utils.ConnectionPool[aiohttp.ClientWebSocketResponse] | None
        ) = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def _ensure_small_pool(
        self,
    ) -> utils.ConnectionPool[aiohttp.ClientWebSocketResponse]:
        if self._small_pool is None:
            self._small_pool = utils.ConnectionPool[
                aiohttp.ClientWebSocketResponse
            ](
                connect_cb=self._connect_small_ws,
                close_cb=self._close_small_ws,
                max_session_duration=30,
                mark_refreshed_on_get=False,
            )
        return self._small_pool

    async def _connect_small_ws(
        self, timeout: float
    ) -> aiohttp.ClientWebSocketResponse:
        session = self._ensure_session()
        return await asyncio.wait_for(
            session.ws_connect(
                self._opts.get_ws_url(),
                headers=self._opts.get_ws_header(),
            ),
            timeout=timeout,
        )

    async def _close_small_ws(
        self, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        await ws.close()

    def synthesize(
        self, text, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ):
        raise NotImplementedError

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        stream = SynthesizeStream(
            tts=self,
            conn_options=conn_options,
            opts=self._opts,
            session=self._ensure_session(),
        )
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        for stream in list(self._streams):
            await stream.aclose()

        self._streams.clear()
        if self._small_pool is not None:
            await self._small_pool.aclose()
            self._small_pool = None


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(
        self,
        *,
        opts: _TTSOptions,
        session: aiohttp.ClientSession,
        tts: TTS,
        conn_options=None,
    ):
        super().__init__(tts=tts, conn_options=conn_options)
        self._opts: _TTSOptions = opts
        self._session = session

    async def _run(self, emitter: tts.AudioEmitter):
        request_id = utils.shortuuid()

        sentence_splitter = TextStreamSentencizer()
        emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            frame_size_ms=200,
            stream=True,
        )

        is_first_sentence = True
        start = time.perf_counter()
        async for token in self._input_ch:
            if isinstance(token, self._FlushSentinel):
                sentences = sentence_splitter.flush()
            else:
                sentences = sentence_splitter.push(text=token)
            for sentence in sentences:
                if len(sentence.strip()) == 0:
                    continue
                if is_first_sentence:
                    is_first_sentence = False
                    elapsed_time = time.perf_counter() - start
                    logger.info(
                        "llm first sentence", extra={"spent": round(elapsed_time, 4)}
                    )
                logger.info("tts start", extra={"sentence": sentence})
                emitter.start_segment(segment_id=utils.shortuuid())
                await self._synthesize_sentence(sentence, emitter)
                emitter.end_segment()
                logger.info("tts end")
                self._pushed_text = self._pushed_text.replace(sentence, "")

    async def _synthesize_sentence(
        self, sentence: str, emitter: tts.AudioEmitter
    ) -> None:
        tts_inst: TTS = self._tts
        if tts_inst._use_small_tts:
            await self._synthesize_small(sentence, emitter)
            return

        try:
            await self._synthesize_big(sentence, emitter)
        except ConcurrentQuotaExceededError:
            fallback_voice = infer_fallback_voice(self._opts.voice)
            logger.warning(
                "volcengine tts concurrency exceeded; falling back to small model",
                extra={
                    "voice": self._opts.voice,
                    "fallback_voice": fallback_voice,
                },
            )
            tts_inst._use_small_tts = True
            await self._synthesize_small(sentence, emitter)

    async def _synthesize_big(
        self, sentence: str, emitter: tts.AudioEmitter
    ) -> None:
        reqid = utils.shortuuid()
        payload = self._opts.get_http_request(
            sentence, reqid=reqid, uid=utils.shortuuid()
        )
        headers = self._opts.get_http_header(reqid=reqid)
        first_response = True
        start_time = time.perf_counter()
        async with self._session.post(
            self._opts.get_http_url(),
            json=payload,
            timeout=aiohttp.ClientTimeout(
                total=300,
                sock_connect=self._conn_options.timeout,
            ),
            headers=headers,
        ) as resp:
            if resp.status >= 400:
                error_text = await resp.text()
                if is_concurrency_quota_error(error_text):
                    raise ConcurrentQuotaExceededError(
                        message=f"volcengine tts http error: {error_text}"
                    )
                raise APIStatusError(
                    message=f"volcengine tts http error: {error_text}",
                    status_code=resp.status,
                )
            got_audio = False
            async for line in resp.content:
                if not line:
                    continue
                done, chunk = parse_http_stream_event(line)
                if chunk:
                    if first_response:
                        elapsed_time = time.perf_counter() - start_time
                        logger.info(
                            "tts first response",
                            extra={"spent": round(elapsed_time, 4)},
                        )
                        first_response = False
                    got_audio = True
                    emitter.push(data=chunk)
                if done:
                    break
            if not got_audio:
                raise APIStatusError(
                    message="volcengine tts returned no audio data"
                )

    async def _synthesize_small(
        self, sentence: str, emitter: tts.AudioEmitter
    ) -> None:
        fallback_voice = infer_fallback_voice(self._opts.voice)
        pool = self._tts._ensure_small_pool()

        async def _send_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            data = self._opts.get_ws_query_params(
                sentence, voice=fallback_voice
            )
            await ws.send_bytes(data)

        async def _recv_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            first_response = True
            start_time = time.perf_counter()
            while True:
                res = await ws.receive_bytes()
                done, data = parse_response(res)
                if data is not None:
                    if first_response:
                        logger.info(
                            "tts first response",
                            extra={
                                "spent": round(
                                    time.perf_counter() - start_time, 4
                                )
                            },
                        )
                        first_response = False
                    emitter.push(data=data)
                if done:
                    break

        async with pool.connection(
            timeout=self._conn_options.timeout
        ) as ws:
            tasks = [
                asyncio.create_task(_send_task(ws)),
                asyncio.create_task(_recv_task(ws)),
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                await utils.aio.gracefully_cancel(*tasks)


def parse_http_stream_event(line: bytes) -> tuple[bool, ByteString | None]:
    text = line.decode("utf-8").strip()
    if not text:
        return False, None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise APIStatusError(
            message=f"volcengine tts returned invalid json chunk: {text}"
        ) from exc

    code = int(payload.get("code", -1))
    if code == 0:
        data = payload.get("data")
        if not data:
            return False, None
        return False, base64.b64decode(data)
    if code == 20000000:
        return True, None

    message = payload.get("message", "unknown error")
    full_message = f"volcengine tts server error: {message}"
    if is_concurrency_quota_error(message):
        raise ConcurrentQuotaExceededError(message=full_message)
    raise APIStatusError(message=full_message)


def parse_response(res: bytes) -> Tuple[bool, ByteString | None]:
    header_size = res[0] & 0x0F
    message_type = res[1] >> 4
    message_type_specific_flags = res[1] & 0x0F
    message_compression = res[2] & 0x0F
    payload = res[header_size * 4 :]
    if message_type == 0xB:  # audio-only server response
        if message_type_specific_flags == 0:  # no sequence number as ACK
            return False, None
        else:
            sequence_number = int.from_bytes(payload[:4], "big", signed=True)
            payload = payload[8:]
        if sequence_number < 0:
            return True, payload
        else:
            return False, payload
    elif message_type == 0xF:
        error_msg = payload[8:]
        if message_compression == 1:
            error_msg = gzip.decompress(error_msg)
        error_msg = str(error_msg, "utf-8")
        raise APIStatusError(message=f"volcengine tts server error: {error_msg}")
    elif message_type == 0xC:
        payload = payload[4:]
        if message_compression == 1:
            payload = gzip.decompress(payload)
        return False, None
    else:
        raise APIStatusError(
            message=f"volcengine tts returned unsupported message type: {message_type}"
        )

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
import weakref
from typing import Literal
from urllib.parse import quote

import aiohttp
from aiohttp import WSMsgType
from pydantic import BaseModel, Field

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIError,
    APIStatusError,
    tts,
    utils,
)

from .common import hmac_sha1_base64
from .log import logger

_SIGN_PREFIX = "GETtts.cloud.tencent.com/stream_wsv2"
_WS_BASE = "wss://tts.cloud.tencent.com/stream_wsv2"
_SENTENCE_END = frozenset("。；？！;?!.\n")


def _env_secret_id() -> str | None:
    return os.getenv("TENCENT_TTS_SECRET_ID")


def _env_secret_key() -> str | None:
    return os.getenv("TENCENT_TTS_SECRET_KEY")


def _env_app_id() -> int | None:
    raw = os.getenv("TENCENT_TTS_APP_ID")
    if raw is None or not raw.strip():
        return None
    return int(raw)


class _TencentFlowingOptions(BaseModel):
    app_id: int
    secret_id: str
    secret_key: str
    voice_type: int = 101001
    fast_voice_type: str | None = None
    speed: float = Field(0.0, ge=-2.0, le=6.0)
    volume: float = Field(0.0, ge=-10.0, le=10.0)
    sample_rate: Literal[16000, 8000] = 16000
    codec: Literal["pcm", "mp3"] = "pcm"
    enable_subtitle: bool = False
    emotion_category: str | None = None
    emotion_intensity: int | None = Field(default=None, ge=50, le=200)
    segment_rate: int | None = Field(default=None, ge=0, le=2)


def _sorted_query_for_sign(params: dict[str, str]) -> str:
    return "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))


def build_signed_ws_url(opts: _TencentFlowingOptions) -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    ts = int(time.time())
    expired = ts + 86400

    p: dict[str, str] = {
        "Action": "TextToStreamAudioWSv2",
        "AppId": str(opts.app_id),
        "Codec": opts.codec,
        "Expired": str(expired),
        "SecretId": opts.secret_id,
        "SessionId": session_id,
        "Timestamp": str(ts),
        "VoiceType": str(opts.voice_type),
        "SampleRate": str(opts.sample_rate),
        "Speed": str(opts.speed),
        "Volume": str(opts.volume),
    }
    if opts.enable_subtitle:
        p["EnableSubtitle"] = "True"
    if opts.fast_voice_type:
        p["FastVoiceType"] = opts.fast_voice_type
    if opts.emotion_category:
        p["EmotionCategory"] = opts.emotion_category
    if opts.emotion_intensity is not None:
        p["EmotionIntensity"] = str(opts.emotion_intensity)
    if opts.segment_rate is not None:
        p["SegmentRate"] = str(opts.segment_rate)

    sign_str = f"{_SIGN_PREFIX}?{_sorted_query_for_sign(p)}"
    signature = hmac_sha1_base64(opts.secret_key, sign_str)
    p_with_sig = {**p, "Signature": signature}
    query = "&".join(
        f"{quote(k, safe='')}={quote(str(v), safe='')}" for k, v in sorted(p_with_sig.items())
    )
    return f"{_WS_BASE}?{query}", session_id


def _synthesis_message(session_id: str, text: str) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "message_id": str(uuid.uuid4()),
            "action": "ACTION_SYNTHESIS",
            "data": text,
        },
        ensure_ascii=False,
    )


def _complete_message(session_id: str) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "message_id": str(uuid.uuid4()),
            "action": "ACTION_COMPLETE",
            "data": "",
        },
        ensure_ascii=False,
    )


def _extract_sentences(buffer: str) -> tuple[list[str], str]:
    out: list[str] = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch in _SENTENCE_END:
            seg = buffer[start : i + 1]
            if seg.strip():
                out.append(seg)
            start = i + 1
    return out, buffer[start:]


def _chunk_text_for_synthesis(text: str, max_len: int = 3500) -> list[str]:
    t = text.strip()
    if len(t) <= max_len:
        return [t]
    return [t[i : i + max_len] for i in range(0, len(t), max_len)]


async def _handshake_and_ready(ws: aiohttp.ClientWebSocketResponse) -> None:
    ready = False
    while not ready:
        msg = await ws.receive()
        if msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            code = data.get("code", -1)
            if code != 0:
                raise APIStatusError(
                    message=f"Tencent TTS handshake error: {data.get('message', '')}",
                    status_code=int(code),
                    body=data,
                )
            if data.get("ready") == 1:
                ready = True
            if data.get("final") == 1 and not ready:
                raise APIStatusError(
                    message="Tencent TTS closed before ready",
                    status_code=-1,
                    body=data,
                )
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            raise APIStatusError(
                message="Tencent TTS websocket closed during handshake",
                status_code=-1,
            )


async def _recv_until_final(ws: aiohttp.ClientWebSocketResponse, emitter: tts.AudioEmitter) -> None:
    while True:
        msg = await ws.receive()
        if msg.type == WSMsgType.BINARY:
            emitter.push(msg.data)
        elif msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            code = data.get("code", -1)
            if code != 0:
                raise APIStatusError(
                    message=f"Tencent TTS: {data.get('message', 'error')}",
                    status_code=int(code),
                    body=data,
                )
            if data.get("heartbeat") == 1:
                continue
            if data.get("final") == 1:
                return
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            raise APIStatusError(
                message="Tencent TTS websocket closed before final",
                status_code=-1,
            )


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        app_id: int | None = None,
        secret_id: str | None = None,
        secret_key: str | None = None,
        voice_type: int = 101001,
        fast_voice_type: str | None = None,
        speed: float = 0.0,
        volume: float = 0.0,
        sample_rate: Literal[16000, 8000] = 16000,
        codec: Literal["pcm", "mp3"] = "pcm",
        enable_subtitle: bool = False,
        emotion_category: str | None = None,
        emotion_intensity: int | None = None,
        segment_rate: int | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        resolved_app = app_id if app_id is not None else _env_app_id()
        if resolved_app is None:
            raise ValueError("Tencent TTS app_id is required (or set TENCENT_TTS_APP_ID)")

        sid = secret_id if secret_id is not None else _env_secret_id()
        if not sid:
            raise ValueError("Tencent TTS secret_id is required (or set TENCENT_TTS_SECRET_ID)")

        sk = secret_key if secret_key is not None else _env_secret_key()
        if not sk:
            raise ValueError("Tencent TTS secret_key is required (or set TENCENT_TTS_SECRET_KEY)")

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._opts = _TencentFlowingOptions(
            app_id=resolved_app,
            secret_id=sid,
            secret_key=sk,
            voice_type=voice_type,
            fast_voice_type=fast_voice_type,
            speed=speed,
            volume=volume,
            sample_rate=sample_rate,
            codec=codec,
            enable_subtitle=enable_subtitle,
            emotion_category=emotion_category,
            emotion_intensity=emotion_intensity,
            segment_rate=segment_rate,
        )
        self._session = http_session
        self._streams: weakref.WeakSet[SynthesizeStream] = weakref.WeakSet()

    @property
    def model(self) -> str:
        return str(self._opts.voice_type)

    @property
    def provider(self) -> str:
        return "tencent"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
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


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts
        self._opts = tts._opts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = self._input_text
        if not text.strip():
            raise APIError("Tencent TTS: empty text")

        mime = "audio/pcm" if self._opts.codec == "pcm" else "audio/mpeg"
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            mime_type=mime,
        )

        session = self._tts._ensure_session()
        url, ws_session_id = build_signed_ws_url(self._opts)
        timeout = aiohttp.ClientTimeout(
            total=600,
            sock_connect=self._conn_options.timeout,
        )
        async with session.ws_connect(url, timeout=timeout) as ws:
            await _handshake_and_ready(ws)
            recv = asyncio.create_task(_recv_until_final(ws, output_emitter))
            try:
                for piece in _chunk_text_for_synthesis(text):
                    await ws.send_str(_synthesis_message(ws_session_id, piece))
                await ws.send_str(_complete_message(ws_session_id))
                await recv
            except BaseException:
                recv.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await recv
                raise


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(
        self,
        *,
        tts: TTS,
        conn_options: APIConnectOptions,
        opts: _TencentFlowingOptions,
        session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts
        self._opts = opts
        self._session = session

    async def _run(self, emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        mime = "audio/pcm" if self._opts.codec == "pcm" else "audio/mpeg"
        emitter.initialize(
            request_id=request_id,
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            mime_type=mime,
            frame_size_ms=200,
            stream=True,
        )
        emitter.start_segment(segment_id=utils.shortuuid())

        url, ws_session_id = build_signed_ws_url(self._opts)
        timeout = aiohttp.ClientTimeout(
            total=600,
            sock_connect=self._conn_options.timeout,
        )

        async with self._session.ws_connect(url, timeout=timeout) as ws:
            await _handshake_and_ready(ws)
            recv_task = asyncio.create_task(_recv_until_final(ws, emitter))

            buffer = ""
            first_chunk = True
            t0 = time.perf_counter()

            try:
                async for token in self._input_ch:
                    if isinstance(token, self._FlushSentinel):
                        sentences, buffer = _extract_sentences(buffer)
                        for s in sentences:
                            if first_chunk:
                                first_chunk = False
                                logger.info(
                                    "tencent tts first sentence",
                                    extra={"spent": round(time.perf_counter() - t0, 4)},
                                )
                            await ws.send_str(_synthesis_message(ws_session_id, s))
                        if buffer.strip():
                            await ws.send_str(_synthesis_message(ws_session_id, buffer))
                            buffer = ""
                        continue

                    buffer += token
                    sentences, buffer = _extract_sentences(buffer)
                    for s in sentences:
                        if first_chunk:
                            first_chunk = False
                            logger.info(
                                "tencent tts first sentence",
                                extra={"spent": round(time.perf_counter() - t0, 4)},
                            )
                        await ws.send_str(_synthesis_message(ws_session_id, s))

                    while len(buffer) >= 4000:
                        piece = buffer[:4000]
                        buffer = buffer[4000:]
                        if first_chunk:
                            first_chunk = False
                            logger.info(
                                "tencent tts first chunk",
                                extra={"spent": round(time.perf_counter() - t0, 4)},
                            )
                        await ws.send_str(_synthesis_message(ws_session_id, piece))

                if buffer.strip():
                    if first_chunk:
                        first_chunk = False
                        logger.info(
                            "tencent tts first tail",
                            extra={"spent": round(time.perf_counter() - t0, 4)},
                        )
                    await ws.send_str(_synthesis_message(ws_session_id, buffer))

                await ws.send_str(_complete_message(ws_session_id))
                await recv_task
            except BaseException:
                recv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await recv_task
                raise

        emitter.end_segment()

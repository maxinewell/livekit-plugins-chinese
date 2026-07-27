import base64
import gzip
import json

import pytest

from livekit.agents import APIStatusError
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins.volcengine.tts import (
    SynthesizeStream,
    TTS,
    _TTSOptions,
    ConcurrentQuotaExceededError,
    infer_fallback_voice,
    infer_resource_id,
    is_concurrency_quota_error,
    parse_http_stream_event,
    parse_response,
)


class _DummyStream:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_volcengine_tts_aclose_closes_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    tts = TTS(app_id="app", access_token="token", http_session=object())
    dummy_stream = _DummyStream()

    def fake_stream(**kwargs):
        return dummy_stream

    monkeypatch.setattr("livekit.plugins.volcengine.tts.SynthesizeStream", fake_stream)

    stream = tts.stream(conn_options=object())
    assert stream is dummy_stream

    await tts.aclose()

    assert dummy_stream.closed is True


def test_volcengine_tts_parse_response_raises_on_error_message() -> None:
    error_text = "permission denied".encode("utf-8")
    payload = bytearray(8)
    payload.extend(error_text)
    packet = bytearray(b"\x11\xf0\x00\x00")
    packet.extend(payload)

    with pytest.raises(APIStatusError, match="permission denied"):
        parse_response(bytes(packet))


def test_volcengine_tts_parse_response_raises_on_unknown_message_type() -> None:
    packet = bytearray(b"\x11\xd0\x00\x00")

    with pytest.raises(APIStatusError, match="unsupported message type"):
        parse_response(bytes(packet))


def test_volcengine_tts_uses_v3_endpoint() -> None:
    opts = _TTSOptions(app_id="app", access_token="token")

    assert opts.get_http_url() == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def test_volcengine_tts_http_header_uses_v3_auth() -> None:
    headers = _TTSOptions(
        app_id="app",
        access_token="token",
        voice="zh_female_xiaohe_uranus_bigtts",
    ).get_http_header(reqid="request-id")

    assert headers["X-Api-App-Key"] == "app"
    assert headers["X-Api-Access-Key"] == "token"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert headers["X-Api-Request-Id"] == "request-id"
    assert headers["Content-Type"] == "application/json"


def test_volcengine_tts_http_payload_uses_submit_operation() -> None:
    payload = _TTSOptions(
        app_id="app",
        access_token="token",
        voice="voice",
        sample_rate=24000,
    ).get_http_request("hello", reqid="request-id", uid="user-id")

    assert payload["user"]["uid"] == "user-id"
    assert payload["req_params"]["text"] == "hello"
    assert payload["req_params"]["speaker"] == "voice"
    assert payload["req_params"]["audio_params"]["format"] == "pcm"
    assert payload["req_params"]["audio_params"]["sample_rate"] == 24000


def test_volcengine_tts_defaults_to_v3_bigtts_voice() -> None:
    opts = _TTSOptions(app_id="app", access_token="token")

    assert opts.voice == "zh_female_xiaohe_uranus_bigtts"


def test_volcengine_tts_defaults_to_test_resource() -> None:
    headers = _TTSOptions(app_id="app", access_token="token").get_http_header()

    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"


@pytest.mark.parametrize(
    ("voice", "resource_id"),
    [
        ("zh_female_xiaohe_uranus_bigtts", "seed-tts-2.0"),
        ("zh_female_qingxinnvsheng_mars_bigtts", "seed-tts-1.0"),
        ("zh_female_shuangkuaisisi_moon_bigtts", "seed-tts-1.0"),
        ("zh_female_gaolengyujie_emo_v2_mars_bigtts", "seed-tts-1.0"),
        ("S_my_clone_voice", "seed-icl-2.0"),
        ("custom_mix_bigtts", "seed-tts-1.0"),
        ("saturn_zh_female_tob", "seed-tts-2.0"),
    ],
)
def test_infer_resource_id(voice: str, resource_id: str) -> None:
    assert infer_resource_id(voice) == resource_id


def test_volcengine_tts_infers_resource_id_from_voice() -> None:
    headers = _TTSOptions(
        app_id="app",
        access_token="token",
        voice="zh_female_qingxinnvsheng_mars_bigtts",
    ).get_http_header()

    assert headers["X-Api-Resource-Id"] == "seed-tts-1.0"


def test_volcengine_tts_parses_v3_audio_chunk() -> None:
    audio = b"\x00\x01\x02\x03"
    line = (
        '{"code":0,"message":"Success","data":"'
        + base64.b64encode(audio).decode("ascii")
        + '"}\n'
    ).encode("utf-8")

    done, chunk = parse_http_stream_event(line)

    assert done is False
    assert chunk == audio


def test_volcengine_tts_parses_v3_done_chunk() -> None:
    done, chunk = parse_http_stream_event(b'{"code":20000000,"message":"Success"}\n')

    assert done is True
    assert chunk is None


def test_volcengine_tts_raises_on_v3_stream_error() -> None:
    with pytest.raises(APIStatusError, match="voice not found"):
        parse_http_stream_event(b'{"code":3050,"message":"voice not found"}\n')


@pytest.mark.parametrize(
    ("voice", "fallback"),
    [
        ("zh_female_qingxinnvsheng_mars_bigtts", "BV001_streaming"),
        ("zh_female_xiaohe_uranus_bigtts", "BV001_streaming"),
        ("zh_male_m191_uranus_bigtts", "BV002_streaming"),
        ("zh_male_taocheng_uranus_bigtts", "BV002_streaming"),
    ],
)
def test_infer_fallback_voice(voice: str, fallback: str) -> None:
    assert infer_fallback_voice(voice) == fallback


def test_infer_fallback_voice_rejects_unknown_gender() -> None:
    with pytest.raises(ValueError, match="gender"):
        infer_fallback_voice("S_custom_clone_voice")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("quota exceeded for types: concurrency", True),
        ("Quota Exceeded for types: Concurrency", True),
        ("resource ID is mismatched with speaker related resource", False),
        ("permission denied", False),
        ("quota exceeded for types: qps", False),
    ],
)
def test_is_concurrency_quota_error(message: str, expected: bool) -> None:
    assert is_concurrency_quota_error(message) is expected


def test_parse_http_stream_event_raises_concurrency_error() -> None:
    line = b'{"code":45000000,"message":"quota exceeded for types: concurrency"}\n'
    with pytest.raises(ConcurrentQuotaExceededError, match="quota exceeded"):
        parse_http_stream_event(line)


def test_parse_http_stream_event_non_concurrency_stays_api_status() -> None:
    line = b'{"code":55000000,"message":"resource ID is mismatched with speaker related resource"}\n'
    with pytest.raises(APIStatusError) as exc_info:
        parse_http_stream_event(line)
    assert not isinstance(exc_info.value, ConcurrentQuotaExceededError)


def test_small_tts_ws_url() -> None:
    opts = _TTSOptions(app_id="app", access_token="token")
    assert opts.get_ws_url() == "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"


def test_small_tts_ws_header() -> None:
    headers = _TTSOptions(app_id="app", access_token="token").get_ws_header()
    assert headers["Authorization"] == "Bearer;token"


def test_small_tts_ws_payload_uses_fallback_voice_and_cluster() -> None:
    opts = _TTSOptions(app_id="app", access_token="token", sample_rate=16000)
    frame = opts.get_ws_query_params(
        "hello", voice="BV001_streaming", uid="user-id"
    )
    assert frame[:4] == b"\x11\x10\x11\x00"
    payload_size = int.from_bytes(frame[4:8], "big")
    payload = gzip.decompress(frame[8 : 8 + payload_size])
    body = json.loads(payload)
    assert body["app"]["cluster"] == "volcano_tts"
    assert body["app"]["appid"] == "app"
    assert body["audio"]["voice_type"] == "BV001_streaming"
    assert body["audio"]["encoding"] == "pcm"
    assert body["audio"]["rate"] == 16000
    assert body["request"]["operation"] == "submit"
    assert body["request"]["text"] == "hello"


@pytest.mark.asyncio
async def test_tts_falls_back_and_retries_on_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tts = TTS(
        app_id="app",
        access_token="token",
        voice="zh_female_xiaohe_uranus_bigtts",
    )
    calls: list[str] = []

    async def fake_big(self, sentence: str, emitter) -> None:
        calls.append(f"big:{sentence}")
        raise ConcurrentQuotaExceededError(
            message="volcengine tts server error: quota exceeded for types: concurrency"
        )

    async def fake_small(self, sentence: str, emitter) -> None:
        calls.append(f"small:{sentence}")

    monkeypatch.setattr(SynthesizeStream, "_synthesize_big", fake_big)
    monkeypatch.setattr(SynthesizeStream, "_synthesize_small", fake_small)

    stream = SynthesizeStream(
        tts=tts,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        opts=tts._opts,
        session=object(),
    )
    await stream._synthesize_sentence("你好", emitter=object())
    await stream.aclose()

    assert tts._use_small_tts is True
    assert calls == ["big:你好", "small:你好"]


@pytest.mark.asyncio
async def test_tts_sticky_skips_big_model_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tts = TTS(
        app_id="app",
        access_token="token",
        voice="zh_male_m191_uranus_bigtts",
    )
    tts._use_small_tts = True
    calls: list[str] = []

    async def fake_big(self, sentence: str, emitter) -> None:
        calls.append("big")

    async def fake_small(self, sentence: str, emitter) -> None:
        calls.append(f"small:{sentence}")

    monkeypatch.setattr(SynthesizeStream, "_synthesize_big", fake_big)
    monkeypatch.setattr(SynthesizeStream, "_synthesize_small", fake_small)

    stream = SynthesizeStream(
        tts=tts,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        opts=tts._opts,
        session=object(),
    )
    await stream._synthesize_sentence("第二句", emitter=object())
    await stream.aclose()

    assert calls == ["small:第二句"]

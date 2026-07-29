import base64

import pytest
from livekit.agents import APIStatusError
from livekit.plugins.aliyun.tts import (
    TTSOptions,
    make_append_event,
    make_commit_event,
    make_session_finish_event,
    parse_server_event,
)


def test_ws_url_includes_model() -> None:
    opts = TTSOptions(
        api_key="k",
        model="qwen3-tts-flash-realtime",
        voice="Cherry",
        sample_rate=24000,
        language_type="Auto",
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )
    assert (
        opts.get_ws_url()
        == "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime"
    )


def test_ws_header_bearer() -> None:
    opts = TTSOptions(
        api_key="sk-test",
        model="qwen3-tts-flash-realtime",
        voice="Cherry",
        sample_rate=24000,
        language_type="Auto",
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )
    headers = opts.get_ws_header()
    assert headers["Authorization"] == "Bearer sk-test"


def test_session_update_commit_mode() -> None:
    opts = TTSOptions(
        api_key="k",
        model="qwen3-tts-flash-realtime",
        voice="Cherry",
        sample_rate=24000,
        language_type="Auto",
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )
    event = opts.get_session_update_event()
    assert event["type"] == "session.update"
    assert "event_id" in event
    assert event["session"] == {
        "mode": "commit",
        "voice": "Cherry",
        "response_format": "pcm",
        "sample_rate": 24000,
        "language_type": "Auto",
    }


def test_append_commit_finish_events() -> None:
    append = make_append_event("你好。")
    assert append["type"] == "input_text_buffer.append"
    assert append["text"] == "你好。"
    assert "event_id" in append

    commit = make_commit_event()
    assert commit["type"] == "input_text_buffer.commit"
    assert "event_id" in commit

    finish = make_session_finish_event()
    assert finish["type"] == "session.finish"
    assert "event_id" in finish


def test_parse_audio_delta() -> None:
    raw = b"\x00\x01\x02\x03"
    event = {
        "type": "response.audio.delta",
        "delta": base64.b64encode(raw).decode(),
    }
    event_type, chunk = parse_server_event(event)
    assert event_type == "response.audio.delta"
    assert chunk == raw


def test_parse_response_done() -> None:
    event_type, chunk = parse_server_event({"type": "response.done"})
    assert event_type == "response.done"
    assert chunk is None


def test_parse_error_raises() -> None:
    with pytest.raises(APIStatusError, match="bad request"):
        parse_server_event(
            {"type": "error", "error": {"message": "bad request"}}
        )


def test_parse_invalid_base64_raises() -> None:
    with pytest.raises(APIStatusError, match="base64"):
        parse_server_event({"type": "response.audio.delta", "delta": "!!!"})

import pytest

from livekit.agents import APIStatusError
from livekit.plugins.minimax.tts import TTSOptions, _parse_minimax_ws_payload


def test_minimax_tts_ws_url_contains_group_id() -> None:
    opts = TTSOptions(api_key="k", group_id="g1")
    assert opts.get_ws_url().endswith("?GroupId=g1")


def test_minimax_tts_ws_url_without_group_id() -> None:
    opts = TTSOptions(api_key="k")
    assert opts.get_ws_url() == "wss://api.minimaxi.com/ws/v1/t2a_v2"


def test_minimax_tts_request_uses_selected_model() -> None:
    opts = TTSOptions(api_key="k", group_id="g1", model="speech-2.8-hd")
    payload = opts.get_task_start_payload()
    assert payload["model"] == "speech-2.8-hd"


def test_minimax_tts_parse_audio_chunk() -> None:
    done, chunk = _parse_minimax_ws_payload({"data": {"audio": "00010203"}})
    assert done is False
    assert chunk == b"\x00\x01\x02\x03"


def test_minimax_tts_parse_done_chunk() -> None:
    done, chunk = _parse_minimax_ws_payload({"event": "task_continued", "is_final": True})
    assert done is True
    assert chunk is None


def test_minimax_tts_parse_task_finished_done() -> None:
    done, chunk = _parse_minimax_ws_payload({"event": "task_finished"})
    assert done is True
    assert chunk is None


def test_minimax_tts_parse_business_error() -> None:
    with pytest.raises(APIStatusError, match="voice not found"):
        _parse_minimax_ws_payload(
            {"base_resp": {"status_code": 40012, "status_msg": "voice not found"}}
        )


def test_minimax_tts_parse_invalid_hex_audio() -> None:
    with pytest.raises(APIStatusError, match="invalid hex audio chunk"):
        _parse_minimax_ws_payload({"data": {"audio": "zz"}})

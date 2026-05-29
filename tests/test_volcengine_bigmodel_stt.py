import gzip
import json

import pytest

from livekit.plugins import volcengine
from livekit.plugins.volcengine.stt import (
    STTOptions,
    format_stt_server_error,
    lookup_stt_error,
    parse_response,
)


def _decode_full_client_request(payload: bytearray) -> dict:
    header_size = payload[0] & 0x0F
    body = payload[header_size * 4 :]
    body = body[4:]  # sequence
    size = int.from_bytes(body[:4], "big")
    compressed = body[4 : 4 + size]
    return json.loads(gzip.decompress(compressed))


def test_bigmodel_stt_ws_header_includes_connect_id() -> None:
    headers = STTOptions(app_id="app", access_token="token").get_ws_header(
        reqid="request-id"
    )

    assert headers["X-Api-Connect-Id"] == "request-id"


def test_bigmodel_stt_ws_header_accepts_custom_resource_id() -> None:
    headers = STTOptions(
        app_id="app",
        access_token="token",
        resource_id="volc.seedasr.sauc.duration",
    ).get_ws_header(reqid="request-id")

    assert headers["X-Api-Resource-Id"] == "volc.seedasr.sauc.duration"


def test_bigmodel_stt_build_corpus_includes_correct_table() -> None:
    corpus = STTOptions(
        correct_table_id="correct-id-123",
        correct_table_name="my-correct-table",
    )._build_corpus()

    assert corpus == {
        "correct_table_id": "correct-id-123",
        "correct_table_name": "my-correct-table",
    }


def test_bigmodel_stt_full_client_request_includes_correct_table_corpus() -> None:
    payload = STTOptions(
        correct_table_id="correct-id-123",
        correct_table_name="my-correct-table",
    ).get_ws_query_params(uid="test-uid")

    request = _decode_full_client_request(payload)
    assert request["request"]["corpus"] == {
        "correct_table_id": "correct-id-123",
        "correct_table_name": "my-correct-table",
    }


@pytest.mark.parametrize(
    ("code", "meaning"),
    [
        (1001, "请求参数无效"),
        (1012, "音频格式无效"),
        (1020, "识别等待超时"),
        (45000001, "请求参数无效"),
        (45000081, "等包超时"),
        (55000031, "服务器繁忙"),
    ],
)
def test_lookup_stt_error_known_codes(code: int, meaning: str) -> None:
    assert lookup_stt_error(code)[0] == meaning


def test_format_stt_server_error_includes_code_and_server_message() -> None:
    message = format_stt_server_error(
        45000001, {"message": "invalid app key"}
    )

    assert "[45000001]" in message
    assert "请求参数无效" in message
    assert "server: invalid app key" in message


def test_format_stt_server_error_internal_range() -> None:
    message = format_stt_server_error(55000123)

    assert "[55000123]" in message
    assert "服务内部处理错误" in message


def _build_server_error_frame(code: int, payload: dict) -> bytes:
    header = bytes(
        [
            0x11,  # version 1, header size 1
            0xF0,  # SERVER_ERROR_RESPONSE, no flags
            0x10,  # JSON, no compression
            0x00,
        ]
    )
    body = code.to_bytes(4, "big", signed=False)
    payload_bytes = json.dumps(payload).encode("utf-8")
    body += len(payload_bytes).to_bytes(4, "big", signed=False)
    body += payload_bytes
    return header + body


def test_parse_response_error_frame() -> None:
    frame = _build_server_error_frame(45000151, {"message": "bad audio format"})
    parsed = parse_response(frame)

    assert parsed["code"] == 45000151
    assert parsed["payload_msg"]["message"] == "bad audio format"
    assert "音频格式不正确" in format_stt_server_error(
        parsed["code"], parsed["payload_msg"]
    )


def test_volcengine_exports_stt_but_not_bigmodel_stt() -> None:
    assert hasattr(volcengine, "STT")
    assert not hasattr(volcengine, "BigModelSTT")

    with pytest.raises(ImportError):
        exec("from livekit.plugins.volcengine.bigmodel_stt import BigModelSTT")

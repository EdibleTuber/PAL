"""Tests for the message protocol — newline-delimited JSON over unix socket."""
import json

from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    encode_message,
    decode_message,
)


def test_chat_message_round_trip():
    msg = ChatMessage(text="hello")
    encoded = encode_message(msg)
    assert isinstance(encoded, bytes)
    assert encoded.endswith(b"\n")
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, ChatMessage)
    assert decoded.text == "hello"


def test_command_message_round_trip():
    msg = CommandMessage(name="search", args="quantum computing")
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, CommandMessage)
    assert decoded.name == "search"
    assert decoded.args == "quantum computing"


def test_command_message_no_args():
    msg = CommandMessage(name="status", args="")
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, CommandMessage)
    assert decoded.name == "status"
    assert decoded.args == ""


def test_stream_chunk_message_round_trip():
    msg = StreamChunkMessage(token="Hello")
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, StreamChunkMessage)
    assert decoded.token == "Hello"


def test_response_message_round_trip():
    msg = ResponseMessage(text="Here is your answer.", command="status")
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, ResponseMessage)
    assert decoded.text == "Here is your answer."
    assert decoded.command == "status"


def test_error_message_round_trip():
    msg = ErrorMessage(error="Something went wrong")
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, ErrorMessage)
    assert decoded.error == "Something went wrong"


def test_encode_produces_single_line_json():
    msg = ChatMessage(text="line one\nline two")
    encoded = encode_message(msg)
    lines = encoded.strip().split(b"\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["text"] == "line one\nline two"


def test_decode_unknown_type_raises():
    raw = json.dumps({"type": "bogus", "data": 1}).encode()
    try:
        decode_message(raw)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

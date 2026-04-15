"""Tests for the message protocol — newline-delimited JSON over unix socket."""
import json

from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    ResearchProposalMessage,
    ResearchApprovalResponseMessage,
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


def test_tool_progress_roundtrip():
    msg = ToolProgressMessage(tool="read_file", arguments={"path": "Research/quantum.md"})
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, ToolProgressMessage)
    assert decoded.tool == "read_file"
    assert decoded.arguments == {"path": "Research/quantum.md"}
    assert decoded.type == "tool_progress"


def test_decode_unknown_type_raises():
    raw = json.dumps({"type": "bogus", "data": 1}).encode()
    try:
        decode_message(raw)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_research_proposal_message_roundtrip():
    msg = ResearchProposalMessage(
        proposal_id="abc-123",
        topic="prompt injection",
        depth=3,
        rationale="vault has no sources",
    )
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, ResearchProposalMessage)
    assert decoded.proposal_id == "abc-123"
    assert decoded.topic == "prompt injection"
    assert decoded.depth == 3
    assert decoded.rationale == "vault has no sources"
    assert decoded.type == "research_proposal"


def test_research_approval_response_approve():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc-123",
        decision="approve",
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ResearchApprovalResponseMessage)
    assert decoded.proposal_id == "abc-123"
    assert decoded.decision == "approve"
    assert decoded.new_topic is None
    assert decoded.new_depth is None


def test_research_approval_response_edit_carries_new_values():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc-123",
        decision="edit",
        new_topic="refined topic",
        new_depth=5,
    )
    decoded = decode_message(encode_message(msg).strip())
    assert decoded.decision == "edit"
    assert decoded.new_topic == "refined topic"
    assert decoded.new_depth == 5


def test_compile_proposal_message_roundtrip():
    from pal.protocol import CompileProposalMessage
    msg = CompileProposalMessage(
        proposal_id="abc",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="promote findings",
    )
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, CompileProposalMessage)
    assert decoded.proposal_id == "abc"
    assert decoded.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert decoded.rationale == "promote findings"
    assert decoded.type == "compile_proposal"


def test_research_approval_response_carries_summary_paths():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc",
        decision="edit",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ResearchApprovalResponseMessage)
    assert decoded.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert decoded.new_topic is None
    assert decoded.new_depth is None


def test_research_approval_response_summary_paths_defaults_to_none():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc",
        decision="approve",
    )
    decoded = decode_message(encode_message(msg).strip())
    assert decoded.summary_paths is None


def test_reorg_proposal_message_roundtrip():
    from pal.protocol import ReorgProposalMessage
    msg = ReorgProposalMessage(
        proposal_id="abc",
        operations=[
            {"type": "move", "src": "A.md", "dst": "B.md"},
            {"type": "merge", "src": "C.md", "dst": "D.md"},
        ],
        rationale="consolidate and rename",
        references_preview=7,
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ReorgProposalMessage)
    assert decoded.proposal_id == "abc"
    assert decoded.operations == [
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "merge", "src": "C.md", "dst": "D.md"},
    ]
    assert decoded.rationale == "consolidate and rename"
    assert decoded.references_preview == 7
    assert decoded.type == "reorg_proposal"


def test_consolidate_proposal_roundtrip():
    from pal.protocol import ConsolidateProposalMessage, encode_message, decode_message
    msg = ConsolidateProposalMessage(
        proposal_id="abc-123",
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="Merge overlapping notes",
    )
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert decoded == msg

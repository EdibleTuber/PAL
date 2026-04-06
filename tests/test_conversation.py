"""Tests for conversation history management."""
from pal.conversation import Conversation


def test_empty_conversation():
    conv = Conversation(history_depth=10)
    assert conv.messages == []


def test_add_user_message():
    conv = Conversation(history_depth=10)
    conv.add_user("hello")
    assert len(conv.messages) == 1
    assert conv.messages[0] == {"role": "user", "content": "hello"}


def test_add_assistant_message():
    conv = Conversation(history_depth=10)
    conv.add_assistant("hi there")
    assert len(conv.messages) == 1
    assert conv.messages[0] == {"role": "assistant", "content": "hi there"}


def test_history_depth_truncation():
    conv = Conversation(history_depth=4)
    for i in range(6):
        conv.add_user(f"msg {i}")
        conv.add_assistant(f"reply {i}")
    # 12 messages added, depth=4 means keep last 4
    assert len(conv.messages) == 4
    assert conv.messages[0] == {"role": "user", "content": "msg 4"}
    assert conv.messages[-1] == {"role": "assistant", "content": "reply 5"}


def test_get_messages_for_api():
    """get_messages_for_api returns system prompt + conversation history."""
    conv = Conversation(history_depth=10)
    conv.add_user("hello")
    conv.add_assistant("hi")
    system = "You are PAL."
    messages = conv.get_messages_for_api(system_prompt=system)
    assert messages[0] == {"role": "system", "content": "You are PAL."}
    assert messages[1] == {"role": "user", "content": "hello"}
    assert messages[2] == {"role": "assistant", "content": "hi"}


def test_add_tool_call_and_result():
    """Conversation stores assistant tool_calls and tool results."""
    conv = Conversation(history_depth=50)
    conv.add_user("look at quantum.md")

    conv.add_assistant_tool_calls([{
        "id": "call_001",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "Research/quantum.md"}'},
    }])

    conv.add_tool_result("call_001", "# Quantum Computing\n\nQubits are neat.")

    messages = conv.messages
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call_001"
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call_001"
    assert "Qubits" in messages[2]["content"]


def test_clear():
    conv = Conversation(history_depth=10)
    conv.add_user("hello")
    conv.clear()
    assert conv.messages == []

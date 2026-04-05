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


def test_clear():
    conv = Conversation(history_depth=10)
    conv.add_user("hello")
    conv.clear()
    assert conv.messages == []

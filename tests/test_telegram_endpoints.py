"""The topic half of a Telegram destination: ``-100123|42`` is topic 42 in chat -100123."""

from __future__ import annotations

import pytest

from nanomuse.channels.telegram_topics import physical_chat, split_chat, topic_chat


def test_split_chat_pulls_the_topic_off_an_endpoint():
    assert split_chat("-100123|42") == ("-100123", 42)
    assert split_chat("-100123") == ("-100123", 0)
    # a topic id is never 0: "|0" is not an endpoint, it is part of the id
    assert split_chat("-100123|0") == ("-100123|0", 0)
    assert split_chat("42") == ("42", 0)
    assert split_chat("") == ("", 0)


def test_physical_chat_drops_the_topic_only_for_telegram():
    assert physical_chat("telegram", "-100123|42") == "-100123"
    assert physical_chat("telegram", "-100123") == "-100123"
    # another channel's chat id may contain a bar of its own
    assert physical_chat("max", "-100123|42") == "-100123|42"


def test_topic_chat_builds_the_endpoint():
    assert topic_chat("-100123", 42) == "-100123|42"
    assert topic_chat("-100123|42", 7) == "-100123|7"
    assert topic_chat("-100123", None) == "-100123"
    assert topic_chat("-100123", 0) == "-100123"


def test_topic_chat_refuses_what_telegram_cannot_address():
    with pytest.raises(ValueError):
        topic_chat("-100123", -1)
    with pytest.raises(ValueError):
        topic_chat("a-group-name", 5)

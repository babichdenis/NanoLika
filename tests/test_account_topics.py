"""Account topics are created once and never resurrect an archive or unknown request."""

import pytest

from nanomuse.channels.account_picker import (
    AccountChatPicker,
    CatalogUnavailable,
    ConversationUnavailable,
)
from nanomuse.channels.account_topics import AccountTopics


def catalog(account, archived=False):
    return {"account_id": account, "complete": True, "conversations": [
        {"cid": "same-cid", "title": "Chat", "archived": archived},
        {"cid": "old", "title": "Old", "archived": True},
    ]}


def topic_chat(chat, topic):
    return f"{chat}:{topic}"


@pytest.mark.asyncio
async def test_topics_isolate_accounts_and_never_recreate_archived(tmp_path):
    picker = AccountChatPicker(tmp_path / "picker.db")
    topics = AccountTopics(tmp_path / "topics.db", picker)
    calls = []

    async def create(title):
        calls.append(title)
        return len(calls)

    for account, sender in (("a", "1"), ("b", "2")):
        await topics.reconcile(account, sender, sender, catalog(account), create, topic_chat)
    assert calls == ["Chat", "Chat"]
    assert picker.selected("a", "telegram", "1", "1:1", catalog("a")) == "same-cid"
    assert picker.selected("b", "telegram", "2", "2:2", catalog("b")) == "same-cid"
    with pytest.raises(ConversationUnavailable):
        picker.selected("a", "telegram", "1", "1:1", catalog("a", archived=True))
    await topics.reconcile("a", "1", "1", catalog("a", archived=True), create, topic_chat)
    await topics.reconcile("a", "1", "1", catalog("a"), create, topic_chat)
    assert calls == ["Chat", "Chat"]
    with pytest.raises(CatalogUnavailable):
        await topics.reconcile("a", "1", "1", {}, create, topic_chat)


@pytest.mark.asyncio
async def test_timeout_or_restart_never_creates_same_topic_twice(tmp_path):
    picker = AccountChatPicker(tmp_path / "picker.db")
    topics = AccountTopics(tmp_path / "topics.db", picker)
    calls = []

    async def timeout(title):
        calls.append(title)
        raise TimeoutError("Telegram might have accepted")

    await topics.reconcile("a", "1", "1", catalog("a"), timeout, topic_chat)
    restarted = AccountTopics(tmp_path / "topics.db", picker)
    result = await restarted.reconcile("a", "1", "1", catalog("a"), timeout, topic_chat)
    assert calls == ["Chat"] and result == [{"cid": "same-cid", "topic": 0, "state": "unknown"}]


@pytest.mark.asyncio
async def test_existing_topic_is_adopted_without_creating_duplicate(tmp_path):
    picker = AccountChatPicker(tmp_path / "picker.db")
    topics = AccountTopics(tmp_path / "topics.db", picker)
    topics.register("a", "1", "1", "same-cid", 77, catalog("a"))

    async def must_not_create(_title):
        raise AssertionError("existing topic must not be recreated")

    result = await topics.reconcile("a", "1", "1", catalog("a"), must_not_create, topic_chat)
    assert result == [{"cid": "same-cid", "topic": 77, "state": "active"}]
    assert picker.selected("a", "telegram", "1", "1:77", catalog("a")) == "same-cid"


@pytest.mark.asyncio
async def test_old_retired_topic_is_never_recreated_even_if_cloud_chat_is_active(tmp_path):
    picker = AccountChatPicker(tmp_path / "picker.db")
    topics = AccountTopics(tmp_path / "topics.db", picker)
    topics.register("a", "1", "1", "same-cid", 77, catalog("a"), retired=True)

    async def must_not_create(_title):
        raise AssertionError("retired topic must not be recreated")

    result = await topics.reconcile("a", "1", "1", catalog("a"), must_not_create, topic_chat)
    assert result == [{"cid": "same-cid", "topic": 77, "state": "retired"}]
    assert picker.selected("a", "telegram", "1", "1:77", catalog("a")) == ""

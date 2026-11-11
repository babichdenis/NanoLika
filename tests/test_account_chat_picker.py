"""A chat selection always belongs to the verified account and active catalogue."""

import pytest

from nanomuse.channels.account_picker import (
    AccountChatPicker,
    CatalogUnavailable,
    ConversationUnavailable,
)


def catalog(account, archived=False):
    return {"account_id": account, "complete": True, "conversations": [{"cid": "same-cid", "archived": archived}]}


def test_equal_cids_are_separate_per_account_and_surface(tmp_path):
    picker = AccountChatPicker(tmp_path / "picker.db")
    picker.choose("account-a", "telegram", "person-a", "chat:topic", "same-cid", catalog("account-a"))
    picker.choose("account-b", "telegram", "person-b", "chat:topic", "same-cid", catalog("account-b"))
    picker.choose("account-a", "max", "person-a", "chat", "same-cid", catalog("account-a"))
    assert picker.selected("account-a", "telegram", "person-a", "chat:topic", catalog("account-a")) == "same-cid"
    assert picker.selected("account-b", "telegram", "person-b", "chat:topic", catalog("account-b")) == "same-cid"
    assert picker.selected("account-a", "max", "person-a", "chat", catalog("account-a")) == "same-cid"
    assert picker.selected("account-b", "telegram", "person-a", "chat:topic", catalog("account-b")) == ""
    assert (tmp_path / "picker.db").stat().st_mode & 0o777 == 0o600


def test_archive_and_disabled_sync_never_restore_or_overwrite(tmp_path):
    picker = AccountChatPicker(tmp_path / "picker.db")
    picker.choose("a", "telegram", "1", "1:2", "same-cid", catalog("a"))
    with pytest.raises(ConversationUnavailable):
        picker.selected("a", "telegram", "1", "1:2", catalog("a", archived=True))
    with pytest.raises(ConversationUnavailable):
        picker.choose("a", "telegram", "1", "1:2", "same-cid", catalog("a", archived=True))
    with pytest.raises(CatalogUnavailable):
        picker.selected("a", "telegram", "1", "1:2", {})
    with pytest.raises(CatalogUnavailable):
        picker.selected("a", "telegram", "1", "1:2", catalog("b"))
    assert picker.selected("a", "telegram", "1", "1:2", catalog("a")) == "same-cid"

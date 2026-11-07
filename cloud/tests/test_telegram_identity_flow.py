"""Identity is server-verified and survives client changes, not account unlinking."""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_telegram_webapp_session import cloud_fixture

from nanomuse_cloud.browser_session_store import browser_store
from nanomuse_cloud.channel_links import TelegramLogins, _telegram_session
from nanomuse_cloud.service import CloudError


def test_first_webapp_visit_uses_expiring_key_and_same_bot_identity():
    c, _, _ = cloud_fixture(False, bot_open=True)
    first = _telegram_session(c, 123456)
    caller = c.authenticate(first["api_key"])
    assert caller.via == "telegram_webapp" and caller.key_expires_at
    _, bot, _ = c.sign_in_bot("123456")
    again = _telegram_session(c, 123456)
    assert first["account"]["id"] == bot.account_id == again["account"]["id"]
    assert len([k for k in c.db.keys_for(bot.account_id, live_only=True) if k["via"] == "telegram"]) == 1


@pytest.mark.parametrize("unlink", ["account", "chat"])
def test_unlink_revokes_webapp_key_and_cookie_but_keeps_email(unlink):
    c, owner, email = cloud_fixture()
    result = _telegram_session(c, 123456)
    store = browser_store(c)
    cookie, _ = store.issue(c.authenticate(result["api_key"]))
    assert store.caller(cookie).account_id == owner.account_id
    if unlink == "account":
        c.unlink_channel(owner, "telegram")
    else:
        c.unlink_chat("telegram", "123456")
    with pytest.raises(CloudError):
        c.authenticate(result["api_key"])
    assert store.caller(cookie) is None
    assert c.authenticate(email).account_id == owner.account_id


def ticket(c):
    logins = TelegramLogins(c)
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    code, _ = logins.start(challenge)
    return logins, code, verifier


def test_approved_login_cannot_be_switched_to_a_different_telegram():
    c, _, _ = cloud_fixture(False, bot_open=True)
    logins, code, verifier = ticket(c)
    first = logins.approve(code, "123456")
    with pytest.raises(CloudError):
        logins.approve(code, "654321")
    assert logins.poll(code, verifier)["account"]["id"] == first["account"]["id"]


def test_parallel_pkce_poll_issues_one_key_only():
    c, _, _ = cloud_fixture(False, bot_open=True)
    logins, code, verifier = ticket(c)
    logins.approve(code, "123456")
    with pytest.raises(CloudError):
        logins.poll(code, "wrong" * 10)

    def poll():
        try:
            return logins.poll(code, verifier)
        except CloudError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: poll(), range(2)))
    assert sum(result is not None for result in results) == 1

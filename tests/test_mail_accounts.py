"""Several mailboxes at once: the address picks the servers, the tools read each one, and a
send goes out through the mailbox that was asked for."""

from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from nanomuse.config import EmailAccountSettings, EmailSettings, mailbox_secret
from nanomuse.tools.email_tool import ReadEmails, SendEmail
from nanomuse.vault.vault import CredentialVault


@pytest.fixture()
def vault(tmp_path):
    book = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    book.set(mailbox_secret("anna_yandex_ru"), "yandex-pass")
    book.set(mailbox_secret("anna_gmail_com"), "gmail-pass")
    return book


def test_servers_and_secret_follow_the_address():
    account = EmailAccountSettings(address="anna@yandex.ru")
    assert account.id == "anna_yandex_ru"
    assert account.imap_host == "imap.yandex.ru"
    assert account.smtp_port == 465
    assert account.smtp_starttls is False
    assert account.password == "{{vault:EMAIL_ANNA_YANDEX_RU_PASSWORD}}"


def test_a_hand_set_server_wins():
    account = EmailAccountSettings(
        address="anna@example.org", imap_host="mail.example.org", smtp_port=2525
    )
    assert account.imap_host == "mail.example.org"
    assert account.smtp_port == 2525
    assert account.smtp_host == "smtp.example.org"


def test_two_accounts_and_lookup():
    settings = EmailSettings(
        accounts=[
            EmailAccountSettings(address="anna@gmail.com", label="Личное"),
            EmailAccountSettings(address="anna@yandex.ru"),
        ]
    )
    assert [a.address for a in settings.all_accounts()] == ["anna@gmail.com", "anna@yandex.ru"]
    assert settings.account("ЛИЧНОЕ").address == "anna@gmail.com"
    assert settings.account("anna@yandex.ru").id == "anna_yandex_ru"
    assert settings.account().address == "anna@gmail.com"
    assert settings.account("нет такого") is None


def test_the_old_single_mailbox_still_counts():
    settings = EmailSettings(
        enabled=True, address="old@mail.ru", imap_host="imap.mail.ru", smtp_host="smtp.mail.ru"
    )
    accounts = settings.all_accounts()
    assert [a.id for a in accounts] == ["main"]
    assert accounts[0].address == "old@mail.ru"


class FakeIMAP:
    """Just enough of imaplib for one message, and it remembers which host was called."""

    hosts: list[str] = []

    def __init__(self, host, port, timeout=0):
        self.host = host
        FakeIMAP.hosts.append(host)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, address, password):
        self.address = address

    def select(self, mailbox, readonly=True):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return "OK", [b"1"]

    def fetch(self, mid, what):
        raw = f"From: {self.address}\r\nSubject: hi\r\n\r\nbody of the letter".encode()
        return "OK", [(b"1", raw)]

    def logout(self):
        return "BYE"


def test_read_walks_every_mailbox(monkeypatch, vault):
    FakeIMAP.hosts.clear()
    monkeypatch.setattr("nanomuse.tools.email_tool.imaplib.IMAP4_SSL", FakeIMAP)
    settings = EmailSettings(
        accounts=[
            EmailAccountSettings(address="anna@gmail.com"),
            EmailAccountSettings(address="anna@yandex.ru"),
        ]
    )
    result = asyncio.run(ReadEmails(settings=settings, vault=vault).execute(limit=3))
    assert result.ok
    assert "[anna@gmail.com]" in result.output
    assert "[anna@yandex.ru]" in result.output
    assert FakeIMAP.hosts == ["imap.gmail.com", "imap.yandex.ru"]


def test_read_one_mailbox_by_address(monkeypatch, vault):
    FakeIMAP.hosts.clear()
    monkeypatch.setattr("nanomuse.tools.email_tool.imaplib.IMAP4_SSL", FakeIMAP)
    settings = EmailSettings(
        accounts=[
            EmailAccountSettings(address="anna@gmail.com"),
            EmailAccountSettings(address="anna@yandex.ru"),
        ]
    )
    result = asyncio.run(
        ReadEmails(settings=settings, vault=vault).execute(account="anna@yandex.ru")
    )
    assert result.ok
    assert FakeIMAP.hosts == ["imap.yandex.ru"]
    assert "[anna@gmail.com]" not in result.output


class FakeSMTP:
    hosts: list[str] = []

    def __init__(self, host, port, timeout=30):
        self.host = host
        FakeSMTP.hosts.append(host)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, address, password):
        self.address = address

    def send_message(self, message):
        self.message = message

    def quit(self):
        return None


def test_send_goes_out_through_the_named_mailbox(monkeypatch, vault):
    FakeSMTP.hosts.clear()
    monkeypatch.setattr("nanomuse.tools.email_tool.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("nanomuse.tools.email_tool.smtplib.SMTP", FakeSMTP)
    settings = EmailSettings(
        accounts=[
            EmailAccountSettings(address="anna@gmail.com"),
            EmailAccountSettings(address="anna@yandex.ru"),
        ]
    )
    result = asyncio.run(
        SendEmail(settings=settings, vault=vault).execute(
            to="friend@example.com", subject="hello", body="text", account="anna@yandex.ru"
        )
    )
    assert result.ok
    assert FakeSMTP.hosts == ["smtp.yandex.ru"]


def test_the_cli_adds_and_lists_a_mailbox(tmp_path, monkeypatch):
    from nanomuse.cli import app

    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    added = runner.invoke(app, ["mail", "add", "anna@yandex.ru", "--password", "secret"])
    assert added.exit_code == 0, added.output
    listed = runner.invoke(app, ["mail", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert "anna@yandex.ru" in listed.stdout
    assert "imap.yandex.ru" in listed.stdout
    removed = runner.invoke(app, ["mail", "remove", "anna_yandex_ru"])
    assert removed.exit_code == 0, removed.output
    empty = runner.invoke(app, ["mail", "list", "--json"])
    assert empty.stdout.strip() == "[]"


def _json_lines(text: str) -> list:
    import json

    return [
        json.loads(line)
        for line in text.strip().splitlines()
        if line.strip().startswith(("[", "{"))
    ]


def test_a_second_copy_of_the_same_mailbox_is_refused(tmp_path, monkeypatch):
    from nanomuse.cli import app

    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    first = runner.invoke(app, ["mail", "add", "anna@yandex.ru", "--password", "secret"])
    assert first.exit_code == 0, first.output
    again = runner.invoke(app, ["mail", "add", "Anna@Yandex.RU", "--password", "other", "--json"])
    assert again.exit_code == 1
    assert _json_lines(again.stdout)[-1]["code"] == "duplicate"
    listed = runner.invoke(app, ["mail", "list", "--json"])
    assert [row["id"] for row in _json_lines(listed.stdout)[-1]] == ["anna_yandex_ru"]
    replaced = runner.invoke(
        app, ["mail", "add", "anna@yandex.ru", "--password", "other", "--replace"]
    )
    assert replaced.exit_code == 0, replaced.output
    assert [
        row["id"] for row in _json_lines(runner.invoke(app, ["mail", "list", "--json"]).stdout)[-1]
    ] == ["anna_yandex_ru"]


def test_the_cli_edits_a_mailbox_and_carries_its_password(tmp_path, monkeypatch):
    from nanomuse.cli import app

    data = tmp_path / "data"
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(data))
    runner = CliRunner()
    assert (
        runner.invoke(app, ["mail", "add", "anna@yandex.ru", "--password", "secret"]).exit_code == 0
    )
    edited = runner.invoke(
        app,
        [
            "mail",
            "edit",
            "anna_yandex_ru",
            "--address",
            "anna@mail.ru",
            "--label",
            "Работа",
            "--json",
        ],
    )
    assert edited.exit_code == 0, edited.output
    row = _json_lines(edited.stdout)[-1]
    assert row["address"] == "anna@mail.ru"
    assert row["id"] == "anna_mail_ru"
    assert row["label"] == "Работа"
    assert row["imap_host"] == "imap.mail.ru"
    assert row["smtp_port"] == 465
    book = CredentialVault(data / "vault.enc", data / "vault.key")
    assert book.get("EMAIL_ANNA_MAIL_RU_PASSWORD") == "secret"
    assert book.get("EMAIL_ANNA_YANDEX_RU_PASSWORD") is None


def test_the_cli_edits_a_password_after_the_address_moved(tmp_path, monkeypatch):
    from nanomuse.cli import app

    data = tmp_path / "data"
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(data))
    runner = CliRunner()
    assert (
        runner.invoke(app, ["mail", "add", "anna@yandex.ru", "--password", "secret"]).exit_code == 0
    )
    assert (
        runner.invoke(
            app, ["mail", "edit", "anna_yandex_ru", "--address", "anna@gmail.com"]
        ).exit_code
        == 0
    )
    changed = runner.invoke(
        app, ["mail", "edit", "anna@gmail.com", "--password", "fresh", "--json"]
    )
    assert changed.exit_code == 0, changed.output
    assert _json_lines(changed.stdout)[-1]["id"] == "anna_gmail_com"
    book = CredentialVault(data / "vault.enc", data / "vault.key")
    assert book.get("EMAIL_ANNA_GMAIL_COM_PASSWORD") == "fresh"


def test_editing_into_another_mailbox_is_refused(tmp_path, monkeypatch):
    from nanomuse.cli import app

    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    assert runner.invoke(app, ["mail", "add", "anna@yandex.ru", "--password", "one"]).exit_code == 0
    assert runner.invoke(app, ["mail", "add", "anna@gmail.com", "--password", "two"]).exit_code == 0
    clash = runner.invoke(
        app, ["mail", "edit", "anna_yandex_ru", "--address", "anna@gmail.com", "--json"]
    )
    assert clash.exit_code == 1
    assert _json_lines(clash.stdout)[-1]["code"] == "duplicate"
    listed = _json_lines(runner.invoke(app, ["mail", "list", "--json"]).stdout)[-1]
    assert sorted(row["address"] for row in listed) == ["anna@gmail.com", "anna@yandex.ru"]


def test_editing_an_unknown_mailbox_says_so(tmp_path, monkeypatch):
    from nanomuse.cli import app

    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    missing = runner.invoke(app, ["mail", "edit", "nobody@example.org", "--label", "x", "--json"])
    assert missing.exit_code == 1
    assert _json_lines(missing.stdout)[-1]["code"] == "not_found"


def test_the_cli_checks_the_mailbox_it_is_given(tmp_path, monkeypatch):
    """The app passes the address as an argument: `mail test <address>` must take it."""
    from nanomuse.cli import app

    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("imaplib.IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    runner = CliRunner()
    assert (
        runner.invoke(app, ["mail", "add", "anna@yandex.ru", "--password", "secret"]).exit_code == 0
    )
    checked = runner.invoke(app, ["mail", "test", "anna@yandex.ru", "--json"])
    assert checked.exit_code == 0, checked.output
    answer = _json_lines(checked.stdout)[-1]
    assert answer["ok"] is True
    assert answer["id"] == "anna_yandex_ru"
    assert answer["inbox"] == 1

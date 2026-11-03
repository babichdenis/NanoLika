"""The chat list groups by the folder the account knows, as the computer does."""

from types import SimpleNamespace

import pytest

from nanomuse.channels import chat_picker as m


class Menu:
    def __init__(self):
        self.sent = []

    def language(self, msg):
        return "en"

    def ticket(self, msg, action):
        return action

    async def send(self, msg, text, rows):
        self.sent.append((text, rows))


@pytest.mark.asyncio
async def test_chat_list_shows_folders_then_their_chats():
    threads = {
        "t1": SimpleNamespace(id="t1", title="Alpha", folder="NanoLika"),
        "t2": SimpleNamespace(id="t2", title="From telegram", folder="Telegram"),
        "t3": SimpleNamespace(id="t3", title="One more", folder="Telegram"),
    }
    rows = [
        {"cid": "c1", "title": "Alpha", "folder": "NanoLika", "archived": False},
        {"cid": "c2", "title": "From telegram", "folder": "Telegram", "archived": False},
        {"cid": "c3", "title": "One more", "folder": "Telegram", "archived": False},
    ]

    async def catalog(client):
        return {"conversations": rows}

    async def nothing(*args, **kwargs):
        return None

    engine = SimpleNamespace(
        state={"message_cursors": {}},
        pull=nothing,
        client=SimpleNamespace(catalog=catalog, read=nothing),
        catalog=SimpleNamespace(supported=True),
        thread_of=lambda cid: next((t for t in threads.values() if f"c{t.id[-1]}" == cid), None),
        cid_of=lambda tid: {"t1": "c1", "t2": "c2", "t3": "c3"}.get(tid),
    )
    menu = Menu()
    manager = SimpleNamespace(
        svc=SimpleNamespace(sync=engine, threads=threads),
        menu=menu,
        _thread_for=lambda msg: "t1",
    )
    picker = SimpleNamespace(
        m=manager,
        available=lambda msg: _true(),
        _cloud_read=lambda fn, *args: fn(*args),
        reader=lambda channel, chat: "reader",
        selected=lambda channel, chat: "t1",
        topics=SimpleNamespace(enabled=lambda channel: False),
    )
    msg = SimpleNamespace(channel="telegram", chat_id="1")
    picker.available = lambda msg: _true()

    await m.ChatPicker.show(picker, msg)  # type: ignore[arg-type]
    text, buttons = menu.sent[-1]
    assert text.startswith("Folders")
    labels = [row[0][0] for row in buttons]
    assert "NanoLika · 1" in labels and "Telegram · 2" in labels

    await m.ChatPicker.show(picker, msg, 0, False, 1)  # type: ignore[arg-type]
    text, buttons = menu.sent[-1]
    assert text.startswith("Telegram")
    labels = [row[0][0] for row in buttons]
    assert "One more" in labels and "From telegram" in labels
    assert all("NanoLika · 1" not in label for label in labels)


async def _true():
    return True

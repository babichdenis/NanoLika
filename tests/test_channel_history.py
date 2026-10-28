"""Bounded messenger history pages without obsolete topic controls."""

from types import SimpleNamespace

from nanomuse.channels import chat_picker as m


class Client:
    def __init__(self):
        self.calls = []

    async def messages(self, cid, after=0, before=0, limit=8):
        self.calls.append((cid, before, limit))
        all_rows = [{"seq": i + 1, "text": f"line {i + 1}", "role": "user"} for i in range(12)]
        rows = [r for r in all_rows if not before or r["seq"] < before]
        return {"account_id": "account", "messages": rows[-limit:], "has_older": len(rows) > limit}


async def test_history_pages_without_archive_controls():
    sent = []
    client = Client()

    async def send(msg, text, rows):
        sent.append((text, rows))

    engine = SimpleNamespace(
        account_id="account",
        cid_of=lambda tid: "conversation",
        _mine=lambda t: True,
        client=client,
        catalog=SimpleNamespace(rows={"conversation": {"archived": False}}),
    )
    manager = SimpleNamespace(
        svc=SimpleNamespace(sync=engine),
        menu=SimpleNamespace(
            language=lambda msg: "en", ticket=lambda msg, action: action, send=send
        ),
    )
    picker = SimpleNamespace(m=manager)
    thread = SimpleNamespace(id="thread", title="Test chat")
    msg = SimpleNamespace()
    await m.ChatPicker.preview(picker, msg, thread)
    assert client.calls[-1] == ("conversation", 0, 8)
    assert "line 12" in sent[-1][0] and "You: line 1\n" not in sent[-1][0]
    older = sent[-1][1][0][0][1]
    assert older == "chat:history:conversation:5"
    await m.ChatPicker.preview(picker, msg, thread, before=5)
    assert "You: line 1\n" in sent[-1][0] and "line 12" not in sent[-1][0]
    assert all("Earlier" != button[0] for row in sent[-1][1] for button in row)
    assert all(not button[1].startswith("chat:mark:") for row in sent[-1][1] for button in row)
    engine.catalog.rows["conversation"]["archived"] = True
    await m.ChatPicker.preview(picker, msg, thread)
    assert all(not button[1].startswith("chat:mark:") for row in sent[-1][1] for button in row)

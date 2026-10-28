"""MAX transport tests without network calls or model requests."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI, Request

MODULE = Path(__file__).resolve().parents[1] / "nanomuse/channels/max.py"
spec = importlib.util.spec_from_file_location("nanomuse.channels.max", MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Bus:
    def __init__(self):
        self.messages = []
        self.actions = []
        self.status = []

    def inbound(self, x):
        self.messages.append(x)

    def action(self, *x):
        self.actions.append(x)

    def set_status(self, *x):
        self.status.append(x)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.not_ready = 0
        self.require_upload_auth = False

        def api(r):
            self.calls.append(r)
            if r.url.path == "/me":
                return httpx.Response(200, json={"is_bot": True, "username": "fixture"})
            if r.url.path == "/subscriptions":
                return httpx.Response(200, json={"subscriptions": [], "success": True})
            if r.url.path == "/uploads":
                kind = r.url.params.get("type") or "file"
                body = {"url": f"https://upload.invalid/{kind}"}
                if kind != "image":
                    body["token"] = f"token-{kind}"
                return httpx.Response(200, json=body)
            if r.url.host == "upload.invalid":
                if self.require_upload_auth and r.headers.get("authorization") != "fixture-token":
                    return httpx.Response(401, json={"code": "unauthorized"})
                if r.url.path == "/image":
                    return httpx.Response(
                        200, json={"photos": {"photoIds": {"token": "image-token"}}}
                    )
                return httpx.Response(200, text="<retval>1</retval>")
            if r.url.host == "cdn.invalid":
                return httpx.Response(200, content=b"file-bytes")
            if r.url.path.startswith("/videos/"):
                return httpx.Response(200, json={"urls": {"mp4_480": "https://cdn.invalid/v.mp4"}})
            if r.url.path == "/messages" and b"attachments" in r.content and self.not_ready:
                self.not_ready -= 1
                return httpx.Response(400, json={"code": "attachment.not.ready"})
            return httpx.Response(200, json={"message": {"body": {"mid": "fixture-message"}}})

        self.ch = m.MaxChannel(
            {
                "bot_token": "fixture-token",
                "webhook_url": "https://example.invalid/hook",
                "webhook_secret": "s" * 40,
            },
            transport=httpx.MockTransport(api),
        )
        self.bus = Bus()
        await self.ch.start(self.bus)
        app = FastAPI()

        @app.post("/hook")
        async def receive(request: Request):
            return await m.receive_webhook(self.ch, request)

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        )

    async def test_manager_approval_call_accepts_language(self):
        await self.ch.ask("fixture-chat", "approval-fixture", "Confirm action", "ru")
        request = self.calls[-1]
        body = json.loads(request.content)
        self.assertEqual(body["text"], "Confirm action")
        self.assertEqual(body["attachments"][0]["type"], "inline_keyboard")
        buttons = body["attachments"][0]["payload"]["buttons"][0]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(json.loads(buttons[0]["payload"])["approval"], "approval-fixture")

    async def asyncTearDown(self):
        await self.ch.stop()
        await self.client.aclose()

    def update(self, kind="message_created", chat_type="dialog"):
        return {
            "update_type": kind,
            "message": {
                "sender": {"user_id": 17},
                "recipient": {"chat_id": 19, "chat_type": chat_type},
                "body": {"mid": "fixture-mid", "text": "hello"},
            },
        }

    async def post(self, data, secret="s" * 40):
        return await self.client.post("/hook", json=data, headers={"X-Max-Bot-Api-Secret": secret})

    async def test_verified_bot_and_subscription(self):
        self.assertEqual(self.bus.status[-1][1], "connected")
        self.assertTrue(all(r.headers.get("Authorization") == "fixture-token" for r in self.calls))
        self.assertTrue(all("fixture-token" not in str(r.url) for r in self.calls))
        self.assertEqual(json.loads(self.calls[-1].content)["secret"], "s" * 40)

    async def test_forged_webhook_does_not_reach_agent(self):
        self.assertEqual((await self.post(self.update(), "wrong")).status_code, 403)
        self.assertEqual(self.bus.messages, [])

    async def test_retry_does_not_duplicate_message(self):
        for _ in range(3):
            self.assertEqual((await self.post(self.update())).status_code, 200)
        self.assertEqual(len(self.bus.messages), 1)
        self.assertEqual(
            (self.bus.messages[0].channel, self.bus.messages[0].sender_id), ("max", "17")
        )

    async def test_groups_are_not_forwarded(self):
        await self.post(self.update(chat_type="chat"))
        self.assertEqual(self.bus.messages, [])

    async def test_start_is_pairing_input_not_account_claim(self):
        await self.post(
            {"update_type": "bot_started", "chat_id": 19, "user": {"user_id": 17}, "timestamp": 7}
        )
        self.assertEqual(self.bus.messages[0].text, "/start")
        self.assertEqual(self.bus.actions, [])

    async def test_payload_size_and_nested_shape(self):
        self.assertEqual(
            (await self.post({"padding": "x" * (m.MAX_BODY_BYTES + 1)})).status_code, 413
        )
        self.assertEqual(
            (await self.post({"update_type": "message_created", "message": []})).status_code, 200
        )
        self.assertEqual(
            (await self.post({"update_type": "message_created", "message": ["bad"]})).status_code,
            400,
        )
        self.assertEqual(self.bus.messages, [])

    async def test_callback_has_actual_sender(self):
        d = self.update("message_callback")
        d["callback"] = {
            "callback_id": "fixture-callback",
            "user": {"user_id": 17},
            "payload": json.dumps({"approval": "fixture", "decision": "deny"}),
        }
        await self.post(d)
        await self.post(d)
        self.assertEqual(len(self.bus.actions), 1)
        self.assertEqual(self.bus.actions[0][:3], ("max", "19", "17"))

    async def test_stream_and_files_go_as_attachments(self):
        await self.ch.stream("19", "s", "hello", done=False)
        await self.ch.stream("19", "s", "hello world", done=True)
        self.assertEqual(self.calls[-1].method, "PUT")
        self.assertEqual(self.ch._streams, {})
        with tempfile.TemporaryDirectory() as folder:
            png = Path(folder) / "face.png"
            png.write_bytes(b"png")
            clip = Path(folder) / "clip.mp4"
            clip.write_bytes(b"mp4")
            await self.ch.send("19", "caption", files=[str(png), str(clip)])
        uploads = [r for r in self.calls if r.url.path == "/uploads"]
        self.assertEqual([r.url.params.get("type") for r in uploads], ["image", "video"])
        files = [r for r in self.calls if r.url.host == "upload.invalid"]
        self.assertTrue(files)
        self.assertTrue(all("authorization" not in r.headers for r in files))
        sent = [
            json.loads(r.content)
            for r in self.calls
            if r.url.path == "/messages" and r.method == "POST"
        ]
        self.assertEqual(sent[-3], {"text": "caption"})
        self.assertEqual(sent[-2]["attachments"][0]["type"], "image")
        self.assertEqual(sent[-2]["attachments"][0]["payload"]["token"], "image-token")
        self.assertEqual(sent[-1]["attachments"][0]["type"], "video")
        self.assertEqual(sent[-1]["attachments"][0]["payload"]["token"], "token-video")

    async def test_incoming_media_is_downloaded(self):
        update = self.update()
        update["message"]["body"] = {
            "mid": "media-mid",
            "text": "",
            "attachments": [
                {"type": "image", "payload": {"photo_id": 5, "url": "https://cdn.invalid/i.jpg"}},
                {"type": "video", "payload": {"token": "video-token"}},
                {
                    "type": "file",
                    "filename": "doc.pdf",
                    "size": 9,
                    "payload": {"url": "https://cdn.invalid/doc.pdf"},
                },
            ],
        }
        self.assertEqual((await self.post(update)).status_code, 200)
        await asyncio.sleep(0.05)
        msg = self.bus.messages[0]
        self.assertEqual(
            [f.name for f in msg.files], ["photo-5.jpg", "video-video-token.mp4", "doc.pdf"]
        )
        self.assertEqual([f.kind for f in msg.files], ["image", "video", "file"])
        self.assertTrue(all(f.data == b"file-bytes" for f in msg.files))

    async def test_upload_retries_with_the_token_when_the_cdn_asks(self):
        self.require_upload_auth = True
        with tempfile.TemporaryDirectory() as folder:
            png = Path(folder) / "face.png"
            png.write_bytes(b"png")
            await self.ch.send("19", "", files=[str(png)])
        uploads = [r for r in self.calls if r.url.host == "upload.invalid"]
        self.assertEqual(len(uploads), 2)
        self.assertNotIn("authorization", uploads[0].headers)
        self.assertEqual(uploads[1].headers.get("authorization"), "fixture-token")

    async def test_attachment_not_ready_is_retried(self):
        self.not_ready = 1
        with tempfile.TemporaryDirectory() as folder:
            png = Path(folder) / "face.png"
            png.write_bytes(b"png")
            await self.ch.send("19", "", files=[str(png)])
        self.assertEqual(self.not_ready, 0)

    async def test_authentication_error_does_not_leak_token(self):
        def reject(r):
            return httpx.Response(401, json={"message": "fixture-token"})

        ch = m.MaxChannel(self.ch.settings, transport=httpx.MockTransport(reject))
        with self.assertRaises(m.MaxError) as e:
            await ch.verify()
        self.assertNotIn("fixture-token", str(e.exception))


if __name__ == "__main__":
    unittest.main()


class TypingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Tests.asyncSetUp
    asyncTearDown = Tests.asyncTearDown

    async def test_typing_refresh_stops_and_does_not_duplicate(self):
        await self.ch.typing("19", True)
        await asyncio.sleep(0.02)
        calls = [r for r in self.calls if r.url.path == "/chats/19/actions"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0].content), {"action": "typing_on"})
        task = self.ch._typing["19"]
        await self.ch.typing("19", True)
        self.assertIs(self.ch._typing["19"], task)
        await self.ch.typing("19", False)
        self.assertTrue(task.done())
        self.assertNotIn("19", self.ch._typing)

    async def test_stop_cancels_typing_before_closing_transport(self):
        await self.ch.typing("19", True)
        task = self.ch._typing["19"]
        await self.ch.stop()
        self.assertTrue(task.done())
        self.assertEqual(self.ch._typing, {})


class InlineApprovalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Tests.asyncSetUp
    asyncTearDown = Tests.asyncTearDown

    async def test_details_edit_existing_message_and_keep_decision_buttons(self):
        await self.ch.ask_scoped(
            "19", "inline-detail", "Readable operation", "ru", ["once", "always"]
        )
        sent = len([r for r in self.calls if r.method == "POST" and r.url.path == "/messages"])
        await self.ch.expand_approval(
            "19", "inline-detail", "Technical command preview", "ru", ["once", "always"]
        )
        self.assertEqual(
            sent, len([r for r in self.calls if r.method == "POST" and r.url.path == "/messages"])
        )
        request = self.calls[-1]
        self.assertEqual(request.method, "PUT")
        body = json.loads(request.content)
        self.assertIn("Technical command preview", body["text"])
        rows = body["attachments"][0]["payload"]["buttons"]
        self.assertEqual(json.loads(rows[0][0]["payload"])["decision"], "allow")
        self.assertEqual(json.loads(rows[1][0]["payload"])["scope"], "always")
        self.assertTrue(
            all(json.loads(b["payload"])["decision"] != "details" for row in rows for b in row)
        )

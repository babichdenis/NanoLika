import json

import pytest

from nanomuse.bot_projects import (
    BotClient,
    LocalInbox,
    ProjectError,
    connect_project,
    create_project,
    vault,
)
from nanomuse.tools.bot_projects import BotProjects


def test_scaffold_is_local_and_never_overwrites(tmp_path):
    path = tmp_path / "my-bot"
    create_project(path, "telegram", "Мой бот")
    assert (path / "handler.py").exists()
    assert ".private/" in (path / ".gitignore").read_text()
    assert "оплаченным тарифом" in (path / "README.md").read_text()
    with pytest.raises(ProjectError):
        create_project(path, "telegram", "Replacement")
    assert json.loads((path / "bot.json").read_text())["label"] == "Мой бот"


def test_inbox_durable_local_receipt_and_duplicate_detection(tmp_path):
    path = tmp_path / "inbox.db"
    update = {"update_id": 1, "message": {"text": "local private text"}}
    queue = LocalInbox(path)
    queue.store(update)
    queue.close()
    queue = LocalInbox(path)
    queue.store(update)
    assert queue.db.execute("SELECT count(*) FROM inbox").fetchone()[0] == 1
    key, actual = queue.next()
    assert actual == update
    queue.complete(key)
    queue.store(update)
    assert queue.next() is None
    assert "local private text" not in queue.db.execute("SELECT payload FROM inbox").fetchone()[0]
    queue.close()


def test_inbox_failed_jobs_do_not_retry_forever(tmp_path):
    queue = LocalInbox(tmp_path / "inbox.db")
    queue.store({"update_id": 2})
    key, _ = queue.next()
    for _ in range(5):
        queue.failed(key)
    assert queue.next() is None
    assert queue.db.execute("SELECT attempts FROM inbox").fetchone()[0] == 5
    queue.close()


@pytest.mark.asyncio
async def test_connect_keeps_secrets_out_of_source_and_config(tmp_path, monkeypatch):
    path = tmp_path / "bot"
    create_project(path, "max", "Local bot")

    async def request(self, method, endpoint, body=None):
        assert endpoint == "/v1/bot-projects"
        assert body["token"] == "fixture-bot-credential"
        return {"id": "bp_fixture"}

    monkeypatch.setattr(BotClient, "request", request)
    await connect_project(path, "fixture-cloud-credential", "fixture-bot-credential")
    for file in path.iterdir():
        if file.is_file():
            assert "fixture-cloud-credential" not in file.read_text()
            assert "fixture-bot-credential" not in file.read_text()
    assert vault(path).get("bot") == "fixture-bot-credential"


@pytest.mark.asyncio
async def test_agent_tool_refuses_paths_outside_owner_workspace(tmp_path):
    tool = BotProjects(
        workspace=tmp_path / "workspace", credentials=None, gateway_url="https://cloud.nanolika.ru"
    )
    result = await tool.execute(action="create", path="../elsewhere")
    assert result.error
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.asyncio
async def test_unpaid_gateway_has_clear_message_without_remote_secret_echo():
    import httpx

    def upstream(request):
        return httpx.Response(403, json={"error": {"code": "bot_plan_required"}})

    client = BotClient(
        {"platform": "telegram", "gateway": "https://cloud.test", "project_id": "bp_test"},
        "fixture-account",
        "fixture-bot",
        transport=httpx.MockTransport(upstream),
    )
    with pytest.raises(ProjectError, match="оплаченный тариф"):
        await client.call("sendMessage", body={"text": "fixture"})

    def malicious(request):
        return httpx.Response(400, json={"error": {"code": "do-not-echo-fixture-credential"}})

    client.transport = httpx.MockTransport(malicious)
    with pytest.raises(ProjectError) as error:
        await client.call("sendMessage")
    assert "do-not-echo" not in str(error.value)

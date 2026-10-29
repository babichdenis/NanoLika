"""Create/connect bot projects on the owner device; never run uploaded code on Cloud."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ConfigDict

from nanomuse.bot_projects import (
    BotClient,
    ProjectError,
    config,
    connect_project,
    create_project,
    vault,
)
from nanomuse.cloud import CLOUD_KEY
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.workspace_context import active_workspace


class BotProjects(BaseTool):
    name: str = "bot_projects"
    description: str = "Create local Telegram/MAX bot projects, connect them through the paid nanoLika gateway, inspect status, pause/resume. Call guide first for the bot-projects instructions and payment/offline consequences. Code and data stay in the owner workspace. Only an active paid plan with device connectivity opens the gateway. Bot credentials must already be in the local vault; token_ref is a credential name, never a token. A channel owner must add/promote the bot. This tool does not run generated code, bypass device approvals, or host user files on Cloud."
    model_config = ConfigDict(arbitrary_types_allowed=True)
    workspace: Path
    credentials: Any
    gateway_url: str
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["guide", "create", "connect", "status", "pause", "resume"],
            },
            "path": {
                "type": "string",
                "description": "Project folder relative to the owner workspace",
            },
            "platform": {"type": "string", "enum": ["telegram", "max"]},
            "label": {"type": "string"},
            "token_ref": {
                "type": "string",
                "description": "Name of a bot credential already saved securely in the local vault",
            },
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SENSITIVE

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "")
        return CallAssessment(
            risk=RiskLevel.SAFE if action in ("status", "guide") else RiskLevel.SENSITIVE,
            target=str(args.get("path") or ""),
            summary="bot project: " + action,
            egress=action not in ("create", "guide"),
            egress_target="cloud.nanolika.ru"
            if self.gateway_url == "https://cloud.nanolika.ru"
            else None,
            warnings=["Connects a bot or changes its gateway availability"]
            if action in ("connect", "pause", "resume")
            else [],
        )

    async def execute(
        self,
        action: str = "",
        path: str = "",
        platform: str = "telegram",
        label: str = "Мой бот",
        token_ref: str = "",
        **_: Any,
    ) -> ToolResult:
        if action == "guide":
            source = Path(__file__).resolve().parents[1] / "skills/builtin/bot-projects/SKILL.md"
            return ToolResult(output=source.read_text())
        root = active_workspace(self.workspace)
        project = (root / path).resolve()
        if (
            not path
            or project == root
            or not project.is_relative_to(root)
            or not (project / ".private").resolve().is_relative_to(project)
        ):
            return ToolResult.fail(
                "Выберите отдельную папку проекта внутри рабочего пространства владельца"
            )
        try:
            if action == "create":
                result = create_project(project, platform, label)
                data = config(project)
                data["gateway"] = self.gateway_url.rstrip("/")
                (project / "bot.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                )
            elif action == "connect":
                account = self.credentials.get(CLOUD_KEY)
                token = self.credentials.get(token_ref) if token_ref else None
                if not account or not token:
                    return ToolResult.fail(
                        "Нужны вход в nanoLika Cloud и токен бота в защищённом локальном хранилище. Не просите присылать секреты в чат."
                    )
                result = await connect_project(project, account, token)
            else:
                data = config(project)
                kept = vault(project)
                account = kept.get("cloud") or self.credentials.get(CLOUD_KEY)
                if not account:
                    return ToolResult.fail("Войдите в nanoLika Cloud на этом устройстве")
                client = BotClient(data, account, kept.get("bot") or "")
                if action == "status":
                    result = await client.request("GET", "/v1/bot-projects")
                elif action in ("pause", "resume"):
                    result = await client.request(
                        "POST",
                        "/v1/bot-projects/" + data["project_id"] + "/state",
                        {"enabled": action == "resume"},
                    )
                else:
                    return ToolResult.fail("Неизвестное действие проекта бота")
            return ToolResult(output=json.dumps(result, ensure_ascii=False))
        except ProjectError as exc:
            return ToolResult.fail(str(exc))
        except (OSError, ValueError, KeyError):
            return ToolResult.fail(
                "Проект не изменён полностью. Проверьте локальную папку, подключение и статус тарифа; секреты не выводятся."
            )

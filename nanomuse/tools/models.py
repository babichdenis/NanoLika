"""What the account can make, and what it costs before anything is made.

The apps show this in Settings → Models and beside the picture button; the agent had no
way to answer "какие модели мне доступны?" or "сколько будет пять картинок?". This tool
is that answer: the menu the plan allows, the shelf's own choice per kind, and the relay's
estimate for pictures and clips — the same numbers the cabinet prints, nothing charged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nanomuse.cloud import CloudError
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

#: What the pane and the apps understand; the agent repeats these in its answer.
SIZES = "1024x1024, 1536x1024, 1024x1536, 2048x2048"


def _flags(row: dict[str, Any]) -> dict[str, Any]:
    flags = row.get("nanomuse")
    return flags if isinstance(flags, dict) else {}


def _kind(row: dict[str, Any]) -> str:
    """The account's menu puts `kind` at the top; an OpenAI entry keeps it under `nanomuse`."""
    return str(row.get("kind") or _flags(row).get("kind") or "")


def _audio_kind(row: dict[str, Any]) -> str:
    audio = row.get("audio") if isinstance(row.get("audio"), dict) else {}
    return str(audio.get("kind") or _flags(row).get("audio_kind") or "")


def _commerce(row: dict[str, Any]) -> dict[str, Any]:
    commerce = row.get("commerce")
    if not isinstance(commerce, dict):
        commerce = _flags(row).get("commerce")
    return commerce if isinstance(commerce, dict) else {}


def _open(row: dict[str, Any]) -> bool:
    """Whether this plan really opens the model: the menu lists locked rows too."""
    return row.get("available", True) is not False and row.get("plan_locked") is not True


def _rub(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"₽{number:.2f}"


def _money(row: dict[str, Any]) -> str:
    """One line of what a model takes, in the unit the relay prices it by."""
    flags = _flags(row)
    commerce = _commerce(row)
    rates = commerce.get("effective_rub") or commerce.get("rates_rub") or {}
    if not isinstance(rates, dict):
        rates = {}
    kind = _kind(row)
    if kind == "chat":
        return f"вход {_rub(rates.get('input'))} / выход {_rub(rates.get('output'))} за 1M токенов"
    if kind == "image":
        extra = f", 2K {_rub(rates.get('image_2k'))}" if rates.get("image_2k") else ""
        return f"{_rub(rates.get('image'))} за картинку{extra}"
    if kind == "video":
        clip = flags.get("clip_seconds") or row.get("clip_seconds") or 5
        return f"{_rub(rates.get('second'))} за секунду · клип {clip} с"
    if kind == "audio":
        audio_kind = _audio_kind(row)
        if audio_kind == "music":
            return f"{_rub(rates.get('track'))} за трек"
        if audio_kind == "transcribe":
            return f"{_rub(rates.get('minute'))} за минуту записи"
        return f"{_rub(rates.get('speech') or rates.get('minute'))} за озвучку"
    return ""


class Models(BaseTool):
    name: str = "models"
    description: str = (
        "Which models this account can use and what they cost. action=`list` shows the menu "
        "by kind (chat, image, video, audio) with prices in rubles and the shelf's own "
        "choice; action=`estimate` prices pictures or clips before they are made (images, "
        "clips, model, size) and says whether the pool covers it. Nothing is charged. Use "
        "it when the person asks what is available, which model to pick, or how much a "
        "picture or a clip will cost."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "estimate"], "description": "list by default"},
            "kind": {"type": "string", "enum": ["chat", "image", "video", "audio"], "description": "for list"},
            "images": {"type": "integer", "description": "how many pictures to price"},
            "clips": {"type": "integer", "description": "how many clips to price"},
            "model": {"type": "string", "description": "the model to price, optional"},
            "size": {"type": "string", "description": f"picture size: {SIZES}"},
        },
        "required": [],
    }
    risk: RiskLevel = RiskLevel.SAFE
    cloud: Callable[[], Any] | None = None

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(risk=RiskLevel.SAFE, egress=False, summary=f"models {args.get('action') or 'list'}")

    async def execute(
        self,
        action: str = "list",
        kind: str = "",
        images: int = 0,
        clips: int = 0,
        model: str = "",
        size: str = "",
        **_: Any,
    ) -> ToolResult:
        client = self.cloud() if self.cloud is not None else None
        if client is None:
            return ToolResult.fail("Sign in to NanoLika Cloud first.")
        try:
            if str(action) == "estimate":
                answer = await client.estimate(
                    images=max(0, int(images or 0)),
                    image_model=str(model) if str(kind or "image") == "image" else "",
                    size=str(size or ""),
                    clips=max(0, int(clips or 0)),
                    video_model=str(model) if str(kind or "") == "video" else "",
                )
                return self._estimate(answer)
            return await self._list(client, str(kind or ""))
        except CloudError as exc:
            return ToolResult.fail(f"Models failed: {exc.message} ({exc.code})")

    def _estimate(self, answer: dict[str, Any]) -> ToolResult:
        rub = answer.get("rub")
        cny = answer.get("cny")
        bits = [f"Estimate: {_rub(rub)}" + (f" (¥{float(cny):.4f})" if cny is not None else "")]
        for part in answer.get("parts") or []:
            if not isinstance(part, dict):
                continue
            what = "pictures" if part.get("kind") == "image" else "clips"
            bits.append(f"{part.get('count')} {what} · {part.get('model')} · {_rub(part.get('cny'))}")
        if answer.get("unlimited"):
            bits.append("the account has no limit")
        elif answer.get("left_cny") is not None:
            bits.append(f"left in the pool: {_rub(answer.get('left_cny'))}")
            bits.append("covered" if answer.get("affordable") else "not covered by the pool")
        return ToolResult(output=" · ".join(bits))

    async def _list(self, client: Any, kind: str) -> ToolResult:
        menu = await client.me_models()
        rows = menu.get("models") if isinstance(menu, dict) else None
        shelf = menu.get("shelf") if isinstance(menu, dict) else None
        if not isinstance(rows, list):
            rows = await client.models()
        rows = [row for row in rows if isinstance(row, dict)]
        shelf = shelf if isinstance(shelf, dict) else {}
        defaults = shelf.get("default") if isinstance(shelf.get("default"), dict) else {}
        wanted = {kind} if kind in ("chat", "image", "video", "audio") else {"image", "video", "audio"}
        lines: list[str] = []
        for row in rows:
            kind_of = _kind(row)
            if kind_of not in wanted or not _open(row):
                continue
            model_id = str(row.get("id") or "")
            chosen = defaults.get(kind_of) == model_id
            name = str(row.get("name") or model_id)
            audio_kind = _audio_kind(row)
            audio = f" ({audio_kind})" if kind_of == "audio" and audio_kind else ""
            lines.append(f"{model_id} · {name}{audio} · {_money(row)}" + (" · выбран" if chosen else ""))
        if not lines:
            return ToolResult.output("No models of that kind are on this plan.")
        head = "Models on this plan:" if not kind else f"Models on this plan ({kind}):"
        tail = ""
        if "image" in wanted:
            tail = f"\nPicture sizes: {SIZES}."
        return ToolResult(output=head + "\n" + "\n".join(lines) + tail)

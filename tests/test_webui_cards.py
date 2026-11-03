"""A tool's pictures belong on its card: a screenshot the step looked at is part of the
record a person scrolls through, not only something the model saw."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanomuse.schema import Function, ToolCall, ToolResult
from nanomuse.server.events import EventBus, Timeline
from nanomuse.server.webui import WebUI


def ui_for(workspace: Path, timelines: dict[str, Timeline]) -> WebUI:
    return WebUI(EventBus(), lambda thread: timelines[thread], workspace=workspace)  # type: ignore[arg-type]


def a_timeline(tmp_path: Path) -> dict[str, Timeline]:
    return {"main": Timeline("main", tmp_path / "main.json")}


def ui_tool_event_id(ui: WebUI, call: ToolCall) -> str:
    """The card `on_tool_call` opened for this call (the id it patches on the result)."""
    return ui._tool_events[call.id]


def test_a_picture_inside_the_workspace_is_served_relative(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "screenshots").mkdir(parents=True)
    shot = ws / "screenshots" / "1.jpg"
    shot.write_bytes(b"jpg")
    ui = ui_for(ws, a_timeline(tmp_path))
    assert ui._card_images([str(shot), "screenshots/2.jpg"]) == ["screenshots/1.jpg", "screenshots/2.jpg"]


def test_a_picture_outside_the_workspace_is_dropped(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"jpg")
    ui = ui_for(ws, a_timeline(tmp_path))
    assert ui._card_images([str(outside)]) == []


def test_the_tool_event_carries_the_pictures(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "screenshots").mkdir(parents=True)
    shot = ws / "screenshots" / "1.jpg"
    shot.write_bytes(b"jpg")
    timelines = a_timeline(tmp_path)
    ui = ui_for(ws, timelines)
    call = ToolCall(id="c1", function=Function(name="computer_screen", arguments="{}"))
    ui.on_tool_call(call, summary="looked at the screen")
    card = ui_tool_event_id(ui, call)
    ui.on_tool_result(call, ToolResult(output="a window", images=[str(shot)]))
    saved: Any = timelines["main"].get(card)
    assert saved is not None and saved["images"] == ["screenshots/1.jpg"]

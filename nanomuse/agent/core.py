"""The agent loop: think (LLM) → act (tools through the Sentinel) → repeat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from nanomuse import prompts
from nanomuse.calendar import CalendarFeeds
from nanomuse.config import Settings
from nanomuse.contacts import ContactBook
from nanomuse.goals import GoalStore
from nanomuse.llm import session as llm_session
from nanomuse.llm.base import BaseLLM
from nanomuse.llm.vision import keep_newest_images
from nanomuse.logger import logger
from nanomuse.memory import MemoryItem, MemoryStore, MemoryStoreRouter
from nanomuse.runtime import device
from nanomuse.schema import AgentState, Attachment, Message, Role, ToolResult
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.skills import SkillLibrary
from nanomuse.tools.base import ToolCollection
from nanomuse.ui import UI
from nanomuse.workspace_context import active_file_path


class Incoming:
    """A user message on its way to the agent: the text, what was attached to it, the
    language of the client's screens (a BCP-47 tag, "" when the client did not say), and,
    for *Use NanoLika Cloud this time*, the model client this one turn runs on (``llm``)
    and its id (``model``); None and "" for a turn on the configured model."""

    __slots__ = ("customer_scope", "files", "language", "llm", "model", "text")

    def __init__(
        self,
        text: str,
        files: list[Attachment] | None = None,
        language: str = "",
        llm: BaseLLM | None = None,
        model: str = "",
        customer_scope: str = "",
    ):
        self.text = text
        self.files = files or []
        self.language = language
        self.llm = llm
        self.model = model
        self.customer_scope = customer_scope


class MuseAgent:
    def __init__(
        self,
        settings: Settings,
        llm: BaseLLM,
        tools: ToolCollection,
        sentinel: Sentinel,
        ui: UI,
        audit: AuditLog,
        memory: MemoryStore | MemoryStoreRouter | None = None,
        goals: GoalStore | None = None,
        calendar: CalendarFeeds | None = None,
        contacts: ContactBook | None = None,
        skills: SkillLibrary | None = None,
        session_file: Path | None = None,
        conversation_id: str | None = None,
    ):
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.sentinel = sentinel
        self.ui = ui
        self.audit = audit
        self.memory = memory
        self.goals = goals
        self.calendar = calendar
        self.contacts = contacts
        self.skills = skills
        self.session_file = session_file
        # The conversation (thread) this agent is; approvals "for this conversation" are
        # bound to it and last as long as it does. Without one, each run stands alone.
        self.conversation_id = conversation_id
        # The id model calls carry for a vendor that wants one per conversation (OpenCode Go):
        # the thread's, else one of this agent's own for as long as it lives.
        self.session_id = conversation_id or llm_session.new_fallback()
        self.messages: list[Message] = []
        self.state = AgentState.IDLE
        self.turns = 0
        # Optional queue of user messages that arrive *while* a run is in progress (the app
        # lets you interrupt or pile on requests). They are folded into the conversation
        # before the next model call instead of waiting for the current run to finish.
        self.inbox: asyncio.Queue[str | Incoming] | None = None
        self._told_no_vision = False
        # The language of the screens the person reads this conversation on, from the latest
        # message that said (a BCP-47 tag); the language rule names it ahead of the script
        # of one message. "" means no client has said, and the script decides.
        self.ui_language = ""
        # Words for this conversation alone, read when the system prompt is built (the first
        # conversation's phase, nanomuse/server/firstrun.py); None or "" adds nothing.
        self.prompt_addendum: Callable[[], str] | None = None

    def _drain_inbox(self) -> int:
        if self.inbox is None:
            return 0
        count = 0
        while not self.inbox.empty():
            try:
                item = self.inbox.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
            incoming = item if isinstance(item, Incoming) else Incoming(item)
            if incoming.language:
                self.ui_language = incoming.language
            self.messages.append(self.user_message(incoming.text, incoming.files))
            self.audit.record("user_message", content=incoming.text, interjected=True)
            count += 1
        return count

    def user_message(self, text: str, files: list[Attachment] | None = None) -> Message:
        """The user's message as the model gets it: the text, a line per attached file
        (path and what it is), and the pictures as images when the model takes them."""
        if not files:
            return Message.user(text)
        lines = [f"- {a.path} ({a.describe()})" for a in files]
        note = "[Attached files]\n" + "\n".join(lines)
        if any(a.kind != "image" for a in files):
            note += "\nRead them with `files` action=read."
        content = f"{text}\n\n{note}" if text.strip() else note
        images = [
            str(active_file_path(a.path, Path(self.settings.agent.workspace).expanduser()))
            for a in files
            if a.kind == "image"
        ]
        return Message.user(content, images=images or None)

    # ------------------------------------------------------------------ prompt
    def sandbox_note(self) -> str:
        """How commands run, from the shell tool's sandbox (the tools own it)."""
        shell = self.tools.get("shell")
        box = getattr(shell, "sandbox", None)
        if box is not None and box.active:
            note = box.describe().replace("Commands run", "Commands (shell, python_execute) run")
            if box.blocks_network:
                note += (
                    " Commands that need the network say so by what they run (curl, pip, git, a "
                    "URL, a script that imports requests …); otherwise pass network=true."
                )
            return note
        return "Commands (shell, python_execute) run in the workspace with a scrubbed environment."

    def store_section(self) -> str:
        """The store, when its tool is there: where it is and what this build is missing.

        «Missing» is decided by the capsules' own module lists, not by a hand-kept table: a
        capsule whose modules are all importable is not offered, and one that is installed
        stops being mentioned."""
        tool = self.tools.get("store")
        if tool is None:
            return ""
        try:
            missing_lines = getattr(tool, "missing_lines", None)
            if not callable(missing_lines):
                return ""
            missing = missing_lines()
        except Exception as exc:  # noqa: BLE001 — the prompt must never fail on this
            logger.debug("store section skipped: {}", exc)
            return ""
        return prompts.STORE_SECTION.format(base=getattr(tool, "base_url", ""), missing=missing)

    def connectors_section(self) -> str:
        """What this build already has, before the model builds it again.

        The case this exists for: "watch my mail" once sent a model off to write an IMAP
        client and install an MCP server, while the app had the mail connector and mail
        triggers all along — the tools were there, the *statement* that they were was not.
        The lines are built from the settings and the tool list, so a switched-on connector
        is described as on and a switched-off one says where its switch is."""
        s = self.settings
        mail = s.connectors.email

        def tool(name: str) -> bool:
            return self.tools.get(name) is not None

        lines: list[str] = []
        if mail.enabled and mail.imap_host and tool("read_emails"):
            lines.append(prompts.MAIL_ON_SECTION)
        elif tool("triggers"):
            lines.append(prompts.MAIL_OFF_SECTION)
        calendar = s.connectors.calendar
        if calendar.enabled and tool("calendar"):
            lines.append(prompts.CALENDAR_ON_SECTION)
        elif tool("triggers"):
            lines.append(prompts.CALENDAR_OFF_SECTION)
        if not lines:
            return ""
        return prompts.CONNECTORS_SECTION.format(lines="\n".join(lines))

    def capabilities_section(self) -> str:
        """The whole inventory: what this build can do, group by group, one line each.

        Built from the live tool registry (:mod:`nanomuse.capabilities`), so it cannot drift
        from the code. It is what stops the "I cannot do mail" answer to a request the
        connector already serves."""
        from nanomuse import capabilities

        # Connectors and GUI tools can change while this agent keeps the same session.
        inventory = capabilities.inventory(self.tools, self.settings)
        return prompts.CAPABILITIES_SECTION.format(inventory=capabilities.render_prompt(inventory))

    def skills_section(self) -> str:
        """The index of skills — names and descriptions; the tool has the instructions."""
        lib = self.skills
        if lib is None or "skills" not in self.tools:
            return ""
        index = lib.index()
        if not index:
            return ""
        return prompts.SKILLS_SECTION.format(items=index)

    def phone_section(self) -> str:
        """The phone, when GUI operation is on: connected or not, and what is on it."""
        task = self.tools.get("phone_task")
        link = getattr(task, "link", None)
        if task is None or link is None:
            return ""
        device = link.device
        if device is None:
            status = (
                "- GUI operation is on, but no phone is connected right now: the `phone_*` tools "
                "will fail until the user opens the NanoLika app on the phone (or the MobileGym "
                "module). Say so if a step needs the phone."
            )
        else:
            apps = device.app_list()
            status = f"- The user's phone is connected: {device.name} ({device.platform})."
            if apps:
                status += f" Apps on it: {apps}."
        return prompts.PHONE_SECTION.format(status=status)

    def device_section(self) -> str:
        """The phone this runs on, when its capabilities are there as `device__*` tools."""
        from nanomuse.runtime import DEVICE_PREFIX

        names = sorted(
            t.name[len(DEVICE_PREFIX) :] for t in self.tools if t.name.startswith(DEVICE_PREFIX)
        )
        if not names:
            return ""
        return prompts.DEVICE_SECTION.format(tools=", ".join(names))

    def computer_section(self) -> str:
        """This computer's screen, when the hands are on: available or not, and what is in front.
        On a computer with the hands off, one line saying where the switch is — so a request
        for a desktop application gets the way to it, not a shrug."""
        task = self.tools.get("computer_task")
        link = getattr(task, "link", None)
        if task is None or link is None:
            if device() is None and not self.settings.hands.enabled:
                return prompts.COMPUTER_OFF_SECTION
            return ""
        status = link.status() if hasattr(link, "status") else {}
        if not status.get("available", True):
            reason = status.get("reason") or "no backend"
            status_line = (
                "- The hands are turned on, but they cannot drive this computer right now "
                f"({reason}): the `computer_*` tools will fail. Say so if a step needs the screen."
            )
        else:
            last = link.last_screen
            status_line = (
                f"- The hands can drive this computer ({status.get('backend') or 'hands'} on "
                f"{link.device.platform}, screen {link.device.width}×{link.device.height})."
            )
            if last is not None and last.title:
                status_line += f" In front last time: {last.title}."
        return prompts.COMPUTER_SECTION.format(status=status_line)

    def devices_section(self) -> str:
        """The user's other devices, when this computer is on the hub (the `devices` tool)."""
        tool = self.tools.get("devices")
        hub = getattr(tool, "hub", None)
        if tool is None or hub is None:
            return ""
        others = hub.others()
        if not getattr(hub, "client", None) or not hub.client.connected.is_set():
            status = (
                "- This computer is not connected to the NanoLika hub right now, so the other "
                "devices cannot be reached; say so if a step needs one."
            )
        elif not others:
            status = (
                f"- This computer is on the hub as “{hub.device_name}”; no other device of the "
                "user's has joined yet."
            )
        else:
            listed = ", ".join(
                f"{d.get('name')} ({d.get('kind') or 'device'}, "
                f"{'online' if d.get('online') else 'offline'})"
                for d in others
            )
            status = f"- This computer is “{hub.device_name}”. Other devices: {listed}."
        return prompts.DEVICES_SECTION.format(status=status)

    def contacts_note(self) -> str:
        """One line on the address book, when there is one (the tool does the looking up)."""
        book = self.contacts
        if book is None or "contacts" not in self.tools or not book.configured:
            return ""
        n = len(book)
        return (
            f"- Address book: {n} {'person' if n == 1 else 'people'}; look someone up with "
            "`contacts` before writing to them; never guess an address\n"
        )

    async def recall_for(self, user_input: str) -> list[MemoryItem] | None:
        """The memories to put in the system prompt for this message — by meaning too,
        when an embedding endpoint is set up (that is the awaited part)."""
        if self.memory is None or not self.settings.memory.enabled:
            return None
        vault = self.sentinel.vault
        try:
            items = await self.memory.relevant_async(
                user_input, limit=self.settings.memory.max_inject
            )
        except Exception as exc:  # noqa: BLE001 — recall must never stop a turn
            logger.warning("recall failed ({}); using the keyword ranking", exc)
            items = self.memory.relevant(user_input, limit=self.settings.memory.max_inject)
        if vault:
            try:
                return [replace(item, content=vault.redact(item.content)) for item in items]
            except Exception as exc:  # noqa: BLE001 — never send unredacted memories to a model
                logger.warning("memory redaction failed ({}); omitting recalled memory", exc)
                return None
        return items

    def _redact(self, text: str | None) -> str | None:
        vault = self.sentinel.vault
        return vault.redact(text) if vault is not None and text else text

    def _stream_delta(self) -> tuple[Callable[[str], None], Callable[[], None]]:
        vault = self.sentinel.vault
        if vault is None:
            return self.ui.on_text_delta, lambda: None
        redactor = vault.redact_stream()

        def push(text: str) -> None:
            safe = redactor.push(text)
            if safe:
                self.ui.on_text_delta(safe)

        def finish() -> None:
            safe = redactor.finish()
            if safe:
                self.ui.on_text_delta(safe)

        return push, finish

    def language_rule(self, user_input: str) -> str:
        """The `## Language` paragraph of the system prompt. A reply language set under
        Settings wins; else the language of the client's screens when a client has said
        (``ui_language``); else the script of the message."""
        fixed = self.settings.agent.language
        if fixed not in ("", "auto"):
            # the setting holds a tag ("ru", "pt-BR") or a language's own name ("Deutsch");
            # either way the prompt wants that language, never the one the message came in
            named = (
                prompts.language_name(fixed)
                or (fixed if fixed != fixed.lower() else "")
                or prompts.detect_language(user_input)
            )
            return prompts.LANGUAGE_FIXED.format(language=named)
        detected = prompts.detect_language(user_input)
        ui = prompts.language_name(self.ui_language)
        if ui:
            return prompts.LANGUAGE_UI.format(language=ui, detected=detected)
        return prompts.LANGUAGE_AUTO.format(detected=detected)

    def build_system_prompt(
        self, user_input: str, memories_: list[MemoryItem] | None = None
    ) -> str:
        """``memories_`` is what ``recall_for`` returned; without it the keyword ranking is
        used on the spot."""
        a = self.settings.agent
        language_rule = self.language_rule(user_input)
        memories = ""
        if self.memory is not None and self.settings.memory.enabled:
            items = (
                memories_
                if memories_ is not None
                else self.memory.relevant(user_input, limit=self.settings.memory.max_inject)
            )
            if items:
                memories = prompts.MEMORY_SECTION.format(
                    items="\n".join(f"- {m.render()}" for m in items)
                )
                if self.turns <= 1:
                    memories += prompts.MEMORY_OPENING_NOTE
        goals = ""
        if self.goals is not None:
            active = self.goals.list("active")[:5]
            if active:
                lines = []
                for g in active:
                    nxt = g.next_step
                    bits = [f"progress {g.progress}"]
                    if g.category:
                        bits.append(g.category)
                    if g.due:
                        bits.append(f"due {g.due}" + (", overdue" if g.overdue else ""))
                    if nxt:
                        bits.append(f"next: {nxt.idx}. {nxt.title}")
                    if g.proposal:
                        bits.append("a plan change is awaiting the user's answer")
                    lines.append(f"- {g.id}: {g.title} ({', '.join(bits)})")
                goals = prompts.GOALS_SECTION.format(items="\n".join(lines))
        calendar = ""
        if self.calendar is not None and self.calendar.configured:
            # today and tomorrow, from the cache — the tool fetches when more is needed
            today = datetime.now(self.calendar.tz).date()
            events = self.calendar.agenda(today, 2)
            calendar = prompts.CALENDAR_SECTION.format(
                items=self.calendar.render(events, today)
                if events
                else "(nothing on the calendar today or tomorrow)"
            )
        profile = (
            prompts.USER_PROFILE_SECTION.format(profile=a.user_profile.strip())
            if a.user_profile.strip()
            else ""
        )
        extra = (
            f"\n## Additional instructions\n{a.instructions.strip()}\n"
            if a.instructions.strip()
            else ""
        )
        addendum = (self.prompt_addendum() if self.prompt_addendum is not None else "").strip()
        if addendum:
            extra += f"\n## This conversation\n{addendum}\n"
        capabilities = self.capabilities_section()
        return prompts.SYSTEM_PROMPT.format(
            name=a.name,
            language_rule=language_rule,
            now=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%A, UTC%z)"),
            workspace=str(a.workspace.resolve()),
            sentinel_mode=self.settings.sentinel.mode,
            sandbox=self.sandbox_note(),
            contacts=self.contacts_note(),
            tool_names=", ".join(t.name for t in self.tools),
            user_profile=profile,
            memories=memories,
            goals=goals
            + calendar
            + self.device_section()
            + self.phone_section()
            + self.computer_section()
            + self.devices_section()
            + self.skills_section()
            + self.connectors_section()
            + self.store_section(),
            capabilities=capabilities,
            extra=extra,
        )

    # ------------------------------------------------------------------ context window
    def context_messages(self) -> list[Message]:
        """The window the model sees: the newest ``max_context_messages`` (never starting
        inside a tool exchange), with pictures only on the newest ``max_context_images``
        image-bearing messages — a hands session would otherwise send every screenshot of
        the run on every step (C9). Copies where a picture was dropped; the history itself
        keeps them."""
        limit = self.settings.agent.max_context_messages
        msgs = self.messages
        if len(msgs) <= limit:
            window = list(msgs)
        else:
            cutoff = len(msgs) - limit
            # Never start in the middle of a tool exchange: advance to the next user message.
            while cutoff < len(msgs) and msgs[cutoff].role != Role.USER:
                cutoff += 1
            if cutoff >= len(msgs):
                # A single long turn can fill the window. Keep the leading tool
                # exchange whole, even if that takes a few messages over the limit.
                cutoff = slice(-limit, None).indices(len(msgs))[0]
                while 0 < cutoff < len(msgs) and msgs[cutoff].role == Role.TOOL:
                    cutoff -= 1
            window = list(msgs[cutoff:])
        clean: list[Message] = []
        for message in keep_newest_images(window, self.settings.agent.max_context_images):
            copy = message.model_copy(deep=True)
            copy.content = self._redact(copy.content)
            copy.reasoning = self._redact(copy.reasoning)
            if copy.tool_calls:
                for call in copy.tool_calls:
                    call.function.arguments = self._redact(call.function.arguments) or "{}"
            clean.append(copy)
        return clean

    # ------------------------------------------------------------------ stuck detection
    def _is_stuck(self) -> bool:
        assistants = [m for m in self.messages if m.role == Role.ASSISTANT][-3:]
        if len(assistants) < 3:
            return False

        def sig(m: Message) -> str:
            calls = [(tc.function.name, tc.function.arguments) for tc in (m.tool_calls or [])]
            return json.dumps([m.content, calls], ensure_ascii=False, sort_keys=True)

        return len({sig(m) for m in assistants}) == 1

    # ------------------------------------------------------------------ main loop
    async def run(
        self,
        user_input: str,
        purpose: str | None = None,
        files: list[Attachment] | None = None,
        language: str = "",
    ) -> str:
        """One task: a user message (or a background prompt) worked to completion.

        ``purpose`` is what approval cards show as the reason for an action; it defaults
        to the message itself. ``files`` are the attachments that came with the message.
        ``language`` is the BCP-47 tag of the client's screens when the client sent one;
        it is remembered for the conversation and names the reply language ahead of the
        script of the message. Task-scoped approvals end when this call returns.
        """
        if self.state == AgentState.RUNNING:
            raise RuntimeError("agent is already running")
        self.state = AgentState.RUNNING
        self.turns += 1
        if language:
            self.ui_language = language
        if self.skills is not None and "skills" in self.tools:
            # "/weekly-review …" — the skill's instructions ride along with the message
            user_input = self.skills.expand(user_input)
        self.messages.append(self.user_message(user_input, files))
        attached = {"files": [a.path for a in files]} if files else {}
        self.audit.record("user_message", content=user_input, **attached)
        system_prompt = self.build_system_prompt(user_input, await self.recall_for(user_input))
        tool_params = self.tools.to_params()
        final: str | None = None
        step = 0
        empty_replies = 0
        task_token = self.sentinel.begin_task(
            purpose or user_input, conversation=self.conversation_id
        )
        session_token = llm_session.enter(self.session_id)
        try:
            while self.settings.agent.max_steps <= 0 or step < self.settings.agent.max_steps:
                step += 1
                if self._drain_inbox():
                    logger.debug("folded queued user message(s) into the running turn")
                    # the language rule and the memory section follow the latest message
                    latest = self.messages[-1].content or ""
                    system_prompt = self.build_system_prompt(latest, await self.recall_for(latest))
                context = [Message.system(system_prompt), *self.context_messages()]
                on_delta, finish_stream = self._stream_delta()
                response = await self.llm.ask(context, tools=tool_params, on_delta=on_delta)
                finish_stream()
                if (
                    response.finish_reason == "length"
                    and not response.content
                    and not response.tool_calls
                ):
                    # the model thought until the budget ran out (reasoning models do on a
                    # hard step): the same call once more, with room to think and answer
                    logger.info("reply cut off before it began; asking again with more room")
                    on_delta, finish_stream = self._stream_delta()
                    response = await self.llm.ask(
                        context,
                        tools=tool_params,
                        on_delta=on_delta,
                        max_tokens=self.llm.roomier_max_tokens(),
                    )
                    finish_stream()
                if self.llm.vision_available is False and not self._told_no_vision:
                    if any(m.images for m in self.messages if m.role == Role.USER):
                        self._told_no_vision = True
                        self.ui.warn(
                            "This model does not take images, so the picture was described to "
                            "it by name only. It stays in the workspace; a model with vision "
                            "(Settings → Connections) would see it."
                        )
                assistant = response.to_message().model_copy(deep=True)
                assistant.content = self._redact(assistant.content)
                assistant.reasoning = self._redact(assistant.reasoning)
                if assistant.tool_calls:
                    for tool_call in assistant.tool_calls:
                        tool_call.function.arguments = (
                            self._redact(tool_call.function.arguments) or "{}"
                        )
                assistant.meta.update({"step": step, "usage": response.usage})
                self.messages.append(assistant)
                content = self._redact(response.content)
                reasoning = self._redact(response.reasoning)
                self.ui.on_assistant_message(content, reasoning)
                self.audit.record(
                    "assistant_message",
                    step=step,
                    content=content or "",
                    tool_calls=[tc.function.name for tc in response.tool_calls],
                    usage=response.usage,
                )

                if not response.tool_calls:
                    if content:
                        final = content
                        break
                    empty_replies += 1
                    if empty_replies >= 2:
                        final = ""
                        break
                    self.messages.append(
                        Message.user(
                            "(Your reply was empty. Continue the task, or call `terminate` if it is done.)"
                        )
                    )
                    continue

                stop = False
                pictures: list[str] = []
                for call in response.tool_calls:
                    tool = self.tools.get(call.name)
                    if tool is None:
                        summary = f"{call.name}(?)"
                    elif "__raw__" in call.arguments:
                        raw = str(call.arguments["__raw__"])
                        summary = f"{call.name}: arguments cut off ({len(raw)} chars)"
                    else:
                        try:
                            summary = tool.assess(call.arguments).summary
                        except Exception as exc:  # noqa: BLE001
                            # the gate's assess will fail the same way, as a tool result
                            summary = f"{call.name}: bad arguments ({type(exc).__name__})"
                    ui_call = call.model_copy(deep=True)
                    ui_call.function.arguments = self._redact(ui_call.function.arguments) or "{}"
                    self.ui.on_tool_call(ui_call, self._redact(summary) or "")
                    if tool is None:
                        result = ToolResult.fail(
                            f"unknown tool '{call.name}'. Available tools: {', '.join(t.name for t in self.tools)}"
                        )
                    else:
                        result = await self.sentinel.guard(call, tool)
                    self.ui.on_tool_result(ui_call, result)
                    self.messages.append(Message.tool(result.for_model(), call.id, call.name))
                    if result.images:
                        pictures.extend(result.images)
                    if result.stop:
                        final = result.output
                        stop = True
                if stop:
                    break
                if (
                    pictures
                    and self.llm.vision_available is not False
                    and self.settings.llm.vision != "off"
                ):
                    # A tool result cannot carry an image, so the picture (the phone's screen)
                    # follows as a user message; models without vision get the text alone.
                    self.messages.append(
                        Message.user(
                            "[The screenshot that goes with the tool result above.]",
                            images=pictures[-2:],
                        )
                    )
                if self._is_stuck():
                    logger.warning("agent seems stuck – nudging")
                    self.messages.append(Message.user(prompts.STUCK_PROMPT))
            else:
                # Step budget exhausted: ask for a wrap-up without tools.
                self.messages.append(Message.user(prompts.MAX_STEPS_PROMPT))
                context = [Message.system(system_prompt), *self.context_messages()]
                on_delta, finish_stream = self._stream_delta()
                response = await self.llm.ask(context, tools=None, on_delta=on_delta)
                finish_stream()
                assistant = response.to_message().model_copy(deep=True)
                assistant.content = self._redact(assistant.content)
                assistant.reasoning = self._redact(assistant.reasoning)
                if assistant.tool_calls:
                    for tool_call in assistant.tool_calls:
                        tool_call.function.arguments = (
                            self._redact(tool_call.function.arguments) or "{}"
                        )
                self.messages.append(assistant)
                content = self._redact(response.content)
                self.ui.on_assistant_message(content, self._redact(response.reasoning))
                final = content or "(step limit reached)"
            self.state = AgentState.FINISHED
        except Exception:
            self.state = AgentState.ERROR
            raise
        finally:
            llm_session.leave(session_token)
            self.sentinel.end_task(task_token)
            self._save_session()
        return final or ""

    # ------------------------------------------------------------------ session persistence
    def reset(self) -> None:
        self.messages.clear()
        # only this conversation: the taint of the chats running beside this one is theirs
        self.sentinel.untaint(self.conversation_id or "")
        self.state = AgentState.IDLE

    def _save_session(self) -> None:
        if not self.session_file:
            return
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {
                "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "turns": self.turns,
                "messages": [m.model_dump(mode="json") for m in self.messages],
            }
            self.session_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        except OSError as exc:  # pragma: no cover
            logger.warning("could not save session: {}", exc)

    def load_session(self, path: Path) -> int:
        data = json.loads(Path(path).read_text("utf-8"))
        self.messages = [Message.model_validate(m) for m in data.get("messages", [])]
        self.turns = int(data.get("turns", 0))
        self._repair_dangling_tool_calls()
        return len(self.messages)

    def _repair_dangling_tool_calls(self) -> None:
        """A restart in the middle of a tool call (say, while an approval was pending)
        leaves an assistant message whose tool calls have no results. Providers reject
        that history outright, so give every orphan a result that says what happened."""
        repaired: list[Message] = []
        i = 0
        while i < len(self.messages):
            msg = self.messages[i]
            repaired.append(msg)
            i += 1
            if msg.role != Role.ASSISTANT or not msg.tool_calls:
                continue
            answered: set[str] = set()
            while i < len(self.messages) and self.messages[i].role == Role.TOOL:
                repaired.append(self.messages[i])
                answered.add(self.messages[i].tool_call_id or "")
                i += 1
            for call in msg.tool_calls:
                if call.id not in answered:
                    repaired.append(
                        Message.tool(
                            "(no result: the app was restarted before this call finished)",
                            call.id,
                            call.function.name,
                        )
                    )
        if len(repaired) != len(self.messages):
            logger.info("repaired {} unanswered tool call(s)", len(repaired) - len(self.messages))
            self.messages = repaired


__all__ = ["MuseAgent"]

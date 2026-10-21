"""The first conversation on the web (contract C4): the fence, the phase machine, the prompt
addendum for the bound chat only, what a finished turn saves, and the API routes. MockLLM
throughout; no network."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.fences import FENCE_NAMING, clean_name, parse_naming_block, strip_fences
from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server import create_app, firstrun
from nanomuse.server.service import MuseService

ASK = (
    "Lin it is. What would you like to call me?\n"
    '```nanomuse-naming\n{"user_address": "Lin", "suggest": ["Pip", "Wren"]}\n```'
)
NAMED = 'Juno it is.\n\n- one\n- two\n- three\n\n```nanomuse-naming\n{"agent_name": "Juno"}\n```'


# ----------------------------------------------------------------------------- the fence
def test_naming_block_parsing():
    """The desktop's and the iPhone's samples: quotes stripped, duplicates dropped, three at most."""
    text = (
        "What should I call you?\n```nanomuse-naming\n"
        '{"user_address": "“Li”", "suggest": ["Pip", "Pip", "\'Wren\'", "Sol", "Fig"]}\n```'
    )
    block = parse_naming_block(text)
    assert block is not None
    assert block.address_given is True and block.user_address == "Li"
    assert block.suggestions == ["Pip", "Wren", "Sol"]
    assert block.agent_name is None
    zh = parse_naming_block(
        "好的，Lin，记住了！那我呢——你想叫我什么？\n\n```nanomuse-naming\n"
        '{"user_address": "Lin", "suggest": ["豆丁", "小满"]}\n```'
    )
    assert zh is not None and zh.user_address == "Lin" and zh.suggestions == ["豆丁", "小满"]
    none = parse_naming_block(
        'Fine — no name then. What would you like to call me?\n```nanomuse-naming\n{"user_address": null, "suggest": ["Pip", "Wren"]}\n```'
    )
    assert none is not None and none.address_given is True and none.user_address is None
    assert parse_naming_block("今天上海多云，22 度左右。对了——我该怎么称呼你？") is None
    assert parse_naming_block(None) is None
    assert parse_naming_block("") is None
    assert parse_naming_block("```nanomuse-naming\nnot json\n```") is None
    assert parse_naming_block("no block here") is None
    named = parse_naming_block(
        '豆丁，我喜欢这个名字。\n\n- …\n\n```nanomuse-naming\n{"agent_name": "豆丁"}\n```'
    )
    assert named is not None and named.agent_name == "豆丁" and named.address_given is False
    # the last block wins; `a` twice counts once, `d` is beyond the third
    two = parse_naming_block(
        '```nanomuse-naming\n{"agent_name": "A"}\n```\nlater\n```nanomuse-naming\n'
        '{"user_address": "Kai", "suggest": ["a", "b", "c", "d", "a"]}\n```'
    )
    assert two is not None and two.user_address == "Kai" and two.suggestions == ["a", "b", "c"]
    assert two.agent_name is None


def test_clean_name():
    assert clean_name("「豆丁」。") == "豆丁"
    assert clean_name("「小满」。") == "小满"
    assert clean_name(' "Pip" ') == "Pip"
    assert clean_name("  Juno!  ") == "Juno"
    assert clean_name("") is None
    assert clean_name("a name that is far too long to be a name") is None
    assert clean_name("two\nlines") is None
    assert clean_name(42) is None
    assert clean_name(None) is None


def test_strip_fences_removes_the_block_and_a_cut_off_one():
    assert strip_fences(ASK, FENCE_NAMING) == "Lin it is. What would you like to call me?"
    assert strip_fences("plain words", FENCE_NAMING) == "plain words"
    # streaming: the fence has begun but not ended — nothing of it shows
    assert strip_fences('Juno it is.\n```nanomuse-naming\n{"agent_', FENCE_NAMING) == "Juno it is."
    # another tag's fence is left alone
    code = "see:\n```python\nprint(1)\n```"
    assert strip_fences(code, FENCE_NAMING) == code


# ----------------------------------------------------------------------------- the machine
def fresh() -> firstrun.FirstRunState:
    return firstrun.start_conversation(firstrun.FirstRunState(), "main", "en", now=100.0)


def test_start_binds_and_moves_into_the_opening_once():
    s = fresh()
    assert s.phase == "ask_user_name" and s.session_id == "main" and s.started_at == 100.0
    again = firstrun.start_conversation(s, "main", "zh", now=200.0)
    assert again.phase == "ask_user_name" and again.started_at == 100.0 and again.lang == "zh"
    over = firstrun.start_conversation(
        firstrun.FirstRunState(phase="done", chosen="Pip"), "main", "en"
    )
    assert over.phase == "done" and over.chosen == "Pip"


def test_after_turn_follows_the_block_and_nothing_else():
    s = fresh()
    # a reply without a block changes nothing
    same = firstrun.after_turn(s, "Sunny, 22 degrees. And — what should I call you?")
    assert same.state is s and same.show_card is False
    asked = firstrun.after_turn(s, ASK)
    assert asked.state.phase == "ask_agent_name" and asked.show_card is True
    assert asked.address_given == "Lin" and asked.state.user_address == "Lin"
    assert asked.state.suggestions == ["Pip", "Wren"]
    # "no name, thanks": the phase moves, no address to save
    anon = firstrun.after_turn(s, 'Fine.\n```nanomuse-naming\n{"user_address": null}\n```')
    assert anon.state.phase == "ask_agent_name" and anon.address_given is None
    assert anon.address_said is True and anon.state.user_address is None
    # a question asked meanwhile keeps the chooser
    waiting = firstrun.after_turn(asked.state, "Paris is the capital. And my name?")
    assert waiting.state is asked.state and waiting.show_card is True
    # the model took a typed name and already replied as itself: over
    done = firstrun.after_turn(asked.state, NAMED)
    assert done.state.phase == "done" and done.named == "Juno" and done.state.chosen == "Juno"
    assert done.show_card is False and done.state.finished_at > 0


def test_pick_and_dismiss():
    asking = firstrun.after_turn(fresh(), ASK).state
    assert firstrun.pick_name(fresh(), "Pip") is None
    picked = firstrun.pick_name(asking, "Wren")
    assert picked is not None and picked.phase == "named" and picked.chosen == "Wren"
    # the model's first reply as itself ends the ritual, block or not
    assert firstrun.after_turn(picked, "Wren. Here is what I can do…").state.phase == "done"
    assert firstrun.dismiss_chooser(asking).phase == "done"
    assert firstrun.dismiss_chooser(fresh()) is not None
    assert firstrun.dismiss_chooser(fresh()).phase == "ask_user_name"
    assert firstrun.bound_to(asking, "main") and not firstrun.bound_to(asking, "t_1")
    assert not firstrun.bound_to(firstrun.FirstRunState(session_id="main"), "main")
    assert firstrun.running(asking) and not firstrun.running(firstrun.dismiss_chooser(asking))


def test_pool_pick_is_stable_distinct_and_the_desktops():
    a = firstrun.built_in_suggestions("session-1", "en")
    assert a == firstrun.built_in_suggestions("session-1", "en") and len(a) == 2 and a[0] != a[1]
    # the same numbers as `builtInSuggestions` in harness/dsh-nanomuse/src/firstrun.ts
    assert a == ["Remy", "Juno"]
    assert firstrun.built_in_suggestions("x", "zh") == ["一一", "叮叮"]
    assert firstrun.hash_code("session-1") == 607795898
    zh = firstrun.built_in_suggestions("x", "zh-CN")
    assert all(n in firstrun.NAME_POOL_ZH for n in zh)
    assert firstrun.current_suggestions(firstrun.after_turn(fresh(), ASK).state, "en") == [
        "Pip",
        "Wren",
    ]


def test_intro_and_addendum_per_phase():
    en = firstrun.intro_lines("en")
    zh = firstrun.intro_lines("zh-CN")
    assert len(en) == 3 and len(zh) == 3
    assert "on this computer" in en[1] and "what should I call you?" in en[2]
    assert "这台电脑" in zh[1] and "称呼" in zh[2]
    s = fresh()
    ask = firstrun.prompt_addendum(s, "en", "NanoLika")
    assert ask is not None and "```nanomuse-naming" in ask and '"user_address"' in ask
    assert "  > Before we start, what should I call you?" in ask
    assert "Siri, Alexa" in ask
    asking = firstrun.after_turn(s, ASK).state
    name = firstrun.prompt_addendum(asking, "en", "NanoLika")
    assert name is not None and '"Pip", "Wren"' in name and '(meaning "Pip")' in name
    assert '"agent_name"' in name and 'goes by "Lin"' in name
    picked = firstrun.pick_name(asking, "Wren")
    assert picked is not None
    named = firstrun.prompt_addendum(picked, "en", "Wren")
    assert named is not None and 'named you "Wren"' in named and "```" not in named
    assert firstrun.prompt_addendum(firstrun.dismiss_chooser(asking), "en", "Wren") is None
    assert firstrun.prompt_addendum(firstrun.FirstRunState(), "en", "NanoLika") is None


def test_address_memory_writes_and_replaces_the_line():
    fresh_text = firstrun.address_memory("", "Li")
    assert fresh_text == "## About the user\n- Call them: Li\n"
    appended = firstrun.address_memory("# GLOBAL\nSome notes.\n\n", "Li")
    assert appended.startswith("# GLOBAL\nSome notes.")
    assert appended.endswith("## About the user\n- Call them: Li\n")
    replaced = firstrun.address_memory(appended, "Wei")
    assert "- Call them: Wei" in replaced and "- Call them: Li" not in replaced
    assert replaced.count("- Call them:") == 1


def test_state_survives_a_tolerant_read(tmp_path):
    store = firstrun.FirstRunStore(tmp_path)
    assert store.state.phase == "none"
    store.save(firstrun.after_turn(fresh(), ASK).state)
    again = firstrun.FirstRunStore(tmp_path)
    assert again.state.phase == "ask_agent_name" and again.state.suggestions == ["Pip", "Wren"]
    assert again.view("en")["chips"] == ["Pip", "Wren"]
    odd = firstrun.FirstRunState.from_dict(
        {"phase": "bogus", "suggestions": "x", "started_at": "y"}
    )
    assert odd.phase == "none" and odd.suggestions == [] and odd.started_at == 0.0
    assert firstrun.FirstRunState.from_dict(None).phase == "none"


# ----------------------------------------------------------------------------- the service
@pytest.fixture()
def server(settings: Settings) -> Iterator[tuple[TestClient, MuseService, MockLLM]]:
    settings.server.token = "secret-token"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        yield client, service, llm


def wait_idle(service: MuseService, thread: str = "main") -> None:
    import time

    t = service.threads[thread]
    deadline = time.time() + 10
    while time.time() < deadline:
        if not t.busy and t.inbox.empty():
            return
        time.sleep(0.02)
    raise AssertionError("the thread did not go idle")


def test_the_first_conversation_over_the_api(server, settings: Settings):
    client, service, llm = server
    # before Start: nothing bound, the opening is still offered in either language
    before = client.get("/api/firstrun?lang=zh").json()
    assert before["phase"] == "none" and before["session_id"] is None
    assert "这台电脑" in before["intro"][1]
    assert client.post("/api/firstrun/pick", json={"name": "Pip"}).status_code == 409

    started = client.post("/api/firstrun/start", json={"lang": "en"}).json()
    assert started["phase"] == "ask_user_name" and started["session_id"] == "main"
    assert started["running"] is True and len(started["intro"]) == 3
    assert started["chips"] == firstrun.built_in_suggestions("main", "en")
    # Start is what the old `onboarded` flag recorded
    assert client.get("/api/connections").json()["onboarded"] is True
    assert client.get("/api/state").json()["firstrun"]["phase"] == "ask_user_name"

    # the model hears the phase in the main chat …
    main = service.threads["main"].agent
    side = service.create_thread("side").agent
    main.prompt_addendum = service._firstrun_addendum_for("main")
    side.prompt_addendum = service._firstrun_addendum_for(side.conversation_id or "")
    assert "First conversation" in main.build_system_prompt("hi")
    assert "First conversation" not in side.build_system_prompt("hi")

    # … and its reply with the block moves the phase, saves the address and the memory line
    llm.script.append(LLMResponse(content=ASK))
    client.post("/api/threads/main/send", json={"text": "Lin"})
    wait_idle(service)
    state = client.get("/api/firstrun").json()
    assert state["phase"] == "ask_agent_name" and state["chips"] == ["Pip", "Wren"]
    assert state["user_address"] == "Lin"
    assert service.profile.user_name == "Lin"
    assert "The user's name is Lin" in settings.agent.instructions
    memories = [m.content for m in service.app.memory.all()]
    assert "Call them: Lin" in memories
    # the reply is stored whole; the web strips the fence when it draws it
    events = client.get("/api/threads/main/events").json()["events"]
    assert events[-1]["type"] == "assistant" and "```nanomuse-naming" in events[-1]["text"]

    # a chip: the name is on the profile at once, the phase is `named`
    picked = client.post("/api/firstrun/pick", json={"name": " Wren "}).json()
    assert picked["phase"] == "named" and picked["chosen"] == "Wren"
    assert service.profile.name == "Wren" and settings.agent.name == "Wren"
    assert 'named you "Wren"' in main.build_system_prompt("Wren")
    assert client.post("/api/firstrun/pick", json={"name": "Pip"}).status_code == 409
    # the model's first reply as itself ends the ritual; the addendum is gone
    llm.script.append(LLMResponse(content="Wren. Here is what I can do…"))
    client.post("/api/threads/main/send", json={"text": "Wren"})
    wait_idle(service)
    over = client.get("/api/firstrun").json()
    assert over["phase"] == "done" and over["running"] is False
    assert "First conversation" not in main.build_system_prompt("hi")
    # a second Start lands on the finished conversation, not a new one
    assert client.post("/api/firstrun/start", json={}).json()["phase"] == "done"


def test_a_typed_name_and_a_bad_pick(server):
    client, service, llm = server
    client.post("/api/firstrun/start", json={"lang": "zh-CN"})
    assert client.get("/api/firstrun").json()["lang"] == "zh"
    llm.script.append(LLMResponse(content=ASK))
    client.post("/api/threads/main/send", json={"text": "Lin"})
    wait_idle(service)
    assert client.post("/api/firstrun/pick", json={"name": "   "}).status_code == 400
    llm.script.append(LLMResponse(content=NAMED))
    client.post("/api/threads/main/send", json={"text": "Juno"})
    wait_idle(service)
    state = client.get("/api/firstrun").json()
    assert state["phase"] == "done" and state["chosen"] == "Juno"
    assert service.profile.name == "Juno"


def test_dismiss_and_a_conversation_already_underway(server):
    client, service, llm = server
    client.post("/api/firstrun/start", json={"lang": "en"})
    llm.script.append(LLMResponse(content=ASK))
    client.post("/api/threads/main/send", json={"text": "Lin"})
    wait_idle(service)
    assert client.post("/api/firstrun/dismiss").json()["phase"] == "done"
    # a main chat that already has turns when Start is pressed is not begun again
    service.firstrun.save(firstrun.FirstRunState())
    again = client.post("/api/firstrun/start", json={"lang": "en"}).json()
    assert again["phase"] == "done" and again["session_id"] == "main"


def test_background_runs_never_hear_the_first_conversation(server):
    client, service, llm = server
    client.post("/api/firstrun/start", json={"lang": "en"})

    def system_prompt() -> str:
        return str(llm.calls[-1]["messages"][0].content or "")

    llm.script.append(LLMResponse(content="nothing new"))
    client.portal.call(service.send, "main", "look around", "system", "Routine: tidy")
    wait_idle(service)
    assert llm.calls and "First conversation" not in system_prompt()
    llm.script.append(LLMResponse(content="Hello. What should I call you?"))
    client.post("/api/threads/main/send", json={"text": "hi"})
    wait_idle(service)
    assert "First conversation" in system_prompt()
    # a reply without the block left the phase where it was
    assert client.get("/api/firstrun").json()["phase"] == "ask_user_name"

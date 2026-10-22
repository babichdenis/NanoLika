"""The own-key guidance (relay 0.21, contract C11): the catalogue copy is the runtime's,
every provider is well-formed, and the ordering follows the person's region."""

from __future__ import annotations

import json
from pathlib import Path

from nanomuse_cloud import providers as pv

ROOT = Path(__file__).resolve().parents[2]
DOCS = "https://nanomuse.cn/own-key"


def test_catalogue_copy_is_the_runtimes():
    """`node scripts/providers-json.mjs` writes the relay's copy; it must be byte-identical
    to the source of truth (CI runs `--check`, this test says the same from Python)."""
    source = ROOT / "nanomuse" / "llm" / "providers.json"
    if not source.exists():  # the relay installed on its own, without the monorepo
        return
    assert pv.CATALOGUE.read_bytes() == source.read_bytes()


def test_catalogue_shape():
    cat = pv.catalogue()
    ids = [p["id"] for p in cat["providers"]]
    assert len(ids) == len(set(ids))
    for must in (
        "bailian",
        "openrouter",
        "openai",
        "anthropic",
        "gemini",
        "deepseek",
        "moonshot",
        "zhipu",
        "siliconflow",
        "volcengine",
        "minimax",
        "xai",
        "groq",
        "mistral",
        "ollama",
        "lm-studio",
        "vllm",
        "custom",
    ):
        assert must in ids
    for p in cat["providers"]:
        assert "chat" in p["capabilities"]
        assert set(p["capabilities"]) <= set(pv.CAPABILITIES)
        assert set(p["regions"]) <= {"cn", "global"}
        assert p["protocol"] in ("openai", "openai-responses", "anthropic", "gemini")
        assert p["verified"] and p["name"] and p["name_zh"]
        for lane, model in p.get("defaults", {}).items():
            need = "vision" if lane == "hands" else lane
            assert need in p["capabilities"], (p["id"], lane)
            assert isinstance(model, str) and model
    # the ChatGPT sign-in gives less than the key: chat and vision only
    openai = pv.provider("openai")
    assert openai is not None
    assert openai["auth_capabilities"]["oauth-chatgpt"] == ["chat", "vision"]
    assert "image" in openai["capabilities"]
    # Bailian is the one key for all four; the local servers are chat until the model says more
    assert pv.provider("bailian")["capabilities"] == ["chat", "vision", "image", "video"]
    for pid in pv.LOCAL:
        assert pv.provider(pid)["capabilities"] == ["chat"]


def test_guidance_keeps_help_and_region_without_promoting_external_services():
    for region in ("cn", "intl", "unknown", "mars"):
        guide = pv.guidance(region, DOCS)
        assert guide["docs"] == DOCS
        assert guide["region"] == region
        for key in ("providers", "session_providers", "plans", "local"):
            assert guide[key] == []
        json.dumps(guide)
    # The own-key catalogue itself remains available to users who choose that slot.
    assert pv.provider("openai") is not None


def test_guidance_retains_sign_in_caveats():
    guide = pv.guidance("intl", DOCS)
    assert "OpenCode" in guide["caveats"]["chatgpt"]
    assert "OpenCode" in guide["caveats"]["chatgpt_zh"]


def test_exhausted_key_line_keeps_the_own_key_option():
    for region in ("cn", "intl", "unknown", "mars"):
        line = pv.exhausted_key_line(region)
        assert "subscription" in line and "invite a friend" in line and "key of your own" in line

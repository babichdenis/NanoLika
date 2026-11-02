"""The thinking-level declaration the menu carries and the wire that carries the choice."""
from nanomuse_cloud.config import ModelSpec
from nanomuse_cloud.reasoning import LEVELS, clean_effort, public_reasoning, reasoning_for


def test_aliases_and_catalogue_ids_get_documented_levels():
    cases = {
        "deepseek-flash": (("low", "high", "max"), "high"),
        "deepseek-pro": (("low", "high", "max"), "high"),
        "gpt-sol": (("low", "medium", "high", "xhigh"), "medium"),
        "gemini-argon": (("low", "medium", "high"), ""),
        "anthropic-claude-opus-5-5": (("low", "medium", "high", "xhigh", "max"), "high"),
        "z-ai-glm-5-3": (("low", "high", "max"), "max"),
        "x-ai-grok-4-7": (("low", "medium", "high", "xhigh"), "high"),
        "google-gemini-3-8-flash": (("low", "medium", "high"), ""),
    }
    for model_id, expected in cases.items():
        spec = ModelSpec(id=model_id, name=model_id, upstream=model_id)
        assert reasoning_for(spec) == expected


def test_a_model_without_documented_levels_has_no_badge():
    spec = ModelSpec(id="qwen3.8-27b", name="Qwen", upstream="qwen3.8-27b")
    assert reasoning_for(spec) == ((), "")
    assert public_reasoning(spec) is None


def test_declared_levels_win_and_are_checked():
    spec = ModelSpec(id="anything", name="Anything", upstream="anything", reasoning_levels=("low", "max"), reasoning_default="max")
    assert reasoning_for(spec) == (("low", "max"), "max")
    assert public_reasoning(spec) == {"levels": ["low", "max"], "default": "max", "wire": "reasoning_effort"}
    try:
        ModelSpec(id="bad", name="Bad", upstream="bad", reasoning_levels=("super",))
    except ValueError as error:
        assert "reasoning_levels" in str(error)
    else:
        raise AssertionError("an unknown level must be refused")


def test_the_wire_takes_only_documented_levels_and_default_means_absence():
    levels = ("low", "high", "max")
    assert clean_effort("high", levels) == "high"
    assert clean_effort("HIGH", levels) == "high"
    for quiet in ("", "default", "auto", "none", "xhigh", None):
        assert clean_effort(quiet, levels) == ""
    assert set(LEVELS) >= {"low", "medium", "high", "max"}

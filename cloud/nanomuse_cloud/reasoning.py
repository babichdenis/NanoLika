"""Thinking levels a model takes, and the wire that carries the choice upstream.

The relay speaks one OpenAI-shaped dialect to every app (`reasoning_effort`), and the
provider behind it may want the same field, a `thinking` toggle beside it, or a nested
`output_config.effort`. The apps only need to know *which levels the model offers and
which one it uses when nobody chose*; the relay does the rest of the translation.

`providers.json` (the own-key catalogue) already documents these levels per vendor, with
the page each rule was verified against. The relay's ids are the aggregator's
(`anthropic-claude-opus-5-5`, `x-ai-grok-4-7`) or the operator's aliases (`deepseek-flash`,
`gpt-sol`), so the same facts are written here as id patterns. A model whose levels are
not documented gets no badge: a choice the provider would refuse is worse than no choice.
"""
from __future__ import annotations

# the harness's own names (pi-ai's thinking levels), strongest last
LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")
# what an app sends: one flat field, the relay translates for the provider behind it
WIRE = "reasoning_effort"

# (pattern, levels, default). First match wins, so narrower patterns come first. The
# default is the level the provider uses when a request says nothing; "" means the
# provider's own default and the app shows "по умолчанию".
RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # the operator's aliases on the relay's shelf
    (r"^deepseek-(flash|pro)$", ("low", "high", "max"), "high"),
    (r"^gpt-sol$", ("low", "medium", "high", "xhigh"), "medium"),
    (r"^gemini-argon$", ("low", "medium", "high"), ""),
    # DeepSeek's own ids and the aggregator's copies
    (r"^deepseek-", ("low", "high", "max"), "high"),
    # OpenAI: o-series, GPT-5 family, GPT-6
    (r"^openai-o[1-9]", ("low", "medium", "high"), "medium"),
    (r"^openai-gpt-5[-.]1", ("low", "medium", "high"), ""),
    (r"^openai-gpt-5[-.]4", ("low", "medium", "high", "xhigh"), ""),
    (r"^openai-gpt-5[-.][235-9]", ("low", "medium", "high", "xhigh"), ""),
    (r"^openai-gpt-5", ("minimal", "low", "medium", "high"), "medium"),
    (r"^openai-gpt-6", ("low", "medium", "high", "xhigh"), ""),
    (r"^openai-gpt-oss-", ("low", "medium", "high"), ""),
    (r"^openai-gpt-(4|4o|chat)", (), ""),
    # Anthropic: every current Claude takes an effort
    (r"^anthropic-claude-", ("low", "medium", "high", "xhigh", "max"), "high"),
    # Google
    (r"^google-gemini-", ("low", "medium", "high"), ""),
    # Z.ai
    (r"^z-ai-glm-5", ("low", "high", "max"), "max"),
    # xAI
    (r"^x-ai-grok-4[-.][6-9]", ("low", "medium", "high", "xhigh"), "high"),
    (r"^x-ai-grok-4[-.]5", ("low", "medium", "high"), "high"),
    # MiniMax
    (r"^minimax-", ("low", "medium", "high", "xhigh", "max"), "max"),
    # Kimi
    (r"^moonshotai-kimi-k3", ("low", "high", "max"), "max"),
    # Mistral, the one family the catalogue documents
    (r"^mistralai-mistral-(small-latest|medium-3-5|large-4-0)", ("high",), ""),
)


def _match(model_id: str) -> tuple[tuple[str, ...], str]:
    import re

    for pattern, levels, default in RULES:
        if re.search(pattern, model_id):
            return levels, default
    return (), ""


def reasoning_for(spec) -> tuple[tuple[str, ...], str]:
    """The levels a model offers: what CLOUD_MODELS declared, else the documented rule."""
    declared = tuple(getattr(spec, "reasoning_levels", ()) or ())
    if declared:
        return declared, str(getattr(spec, "reasoning_default", "") or "")
    if getattr(spec, "kind", "chat") != "chat":
        return (), ""
    for model_id in (str(getattr(spec, "upstream", "") or ""), str(getattr(spec, "id", "") or "")):
        if not model_id:
            continue
        levels, default = _match(model_id)
        if levels:
            return levels, default
    return (), ""


def public_reasoning(spec) -> dict | None:
    """The menu's `reasoning` block, or None when the model documents no choice."""
    levels, default = reasoning_for(spec)
    if not levels:
        return None
    return {"levels": list(levels), "default": default, "wire": WIRE}


def clean_effort(value, levels: tuple[str, ...]) -> str:
    """The effort a request asked for, in the spelling the wire wants.

    `default` (and an empty value) is the app saying "let the provider decide": the field
    is dropped, not sent as a level. A level the model does not document is dropped too —
    a provider behind an aggregator may answer 400 to a name it never offered.
    """
    text = str(value or "").strip().lower()
    if not text or text in ("default", "auto", "none"):
        return ""
    if text in levels:
        return text
    return ""

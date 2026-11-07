"""The Responses slot must not send `input_image` to an OpenAI-compatible gateway."""

from __future__ import annotations

from nanomuse.llm.openai_responses import _image_type


def test_openai_uses_input_image() -> None:
    assert _image_type("https://api.openai.com/v1") == "input_image"


def test_gateways_use_image_url() -> None:
    assert _image_type("https://openrouter.ai/api/v1") == "image_url"
    assert _image_type("https://api.deepseek.com/v1") == "image_url"
    assert _image_type("https://api.polza.ai") == "image_url"

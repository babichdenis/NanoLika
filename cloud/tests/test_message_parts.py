"""The relay sends one Chat Completions shape, whatever names a client used."""

from __future__ import annotations

from nanomuse_cloud.provider_routing import normalize_messages


def test_input_image_becomes_image_url_with_an_object() -> None:
    out = normalize_messages([{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.test/a.png"}]}])
    assert out[0]["content"] == [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]


def test_image_url_string_becomes_an_object() -> None:
    out = normalize_messages([{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.test/a.png"}]}])
    assert out[0]["content"] == [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]


def test_normal_image_url_is_kept() -> None:
    part = {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}
    assert normalize_messages([{"role": "user", "content": [part]}])[0]["content"] == [part]


def test_input_text_becomes_text() -> None:
    out = normalize_messages([{"role": "user", "content": [{"type": "input_text", "text": "привет"}]}])
    assert out[0]["content"] == [{"type": "text", "text": "привет"}]


def test_input_audio_keeps_data_and_format() -> None:
    out = normalize_messages([{"role": "user", "content": [{"type": "input_audio", "data": "AAAA", "format": "wav"}]}])
    assert out[0]["content"] == [{"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}]


def test_bare_string_part_becomes_text() -> None:
    out = normalize_messages([{"role": "user", "content": ["привет"]}])
    assert out[0]["content"] == [{"type": "text", "text": "привет"}]


def test_unknown_text_is_kept_as_text_and_unknown_is_dropped() -> None:
    out = normalize_messages([{"role": "user", "content": [{"type": "mystery", "text": "ok"}, {"type": "nothing"}]}])
    assert out[0]["content"] == [{"type": "text", "text": "ok"}]

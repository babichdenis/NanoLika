"""Markdown from the model as the Bot API's HTML: tags, escaping, and the cuts that keep a
tag from being torn between two messages."""

from __future__ import annotations

from nanomuse.channels import telegram_format as fmt


def test_bold_italic_and_code():
    html = fmt.render("**жирный** и __тоже жирный__ и *курсив* и `код`")
    assert "<b>жирный</b>" in html
    assert "<b>тоже жирный</b>" in html
    assert "<i>курсив</i>" in html
    assert "<code>код</code>" in html


def test_a_link_keeps_its_address_and_is_not_italicized():
    html = fmt.render("см. [документ](https://example.com/a_b_c?x=1&y=2)")
    assert '<a href="https://example.com/a_b_c?x=1&amp;y=2">документ</a>' in html


def test_headings_and_lists():
    html = fmt.render("# Итоги дня\n\n- первое\n- второе\n\n1. шаг\n2. шаг")
    assert "<b>Итоги дня</b>" in html
    assert "• первое" in html and "• второе" in html
    assert "1. шаг" in html and "2. шаг" in html


def test_a_code_block_becomes_pre_and_is_not_formatted_inside():
    html = fmt.render("```python\nprint('**не жирный**')\n```")
    assert html == "<pre>print('**не жирный**')</pre>"


def test_script_and_ampersand_are_escaped_before_the_markup():
    html = fmt.render("<script>alert(1)</script> & текст")
    assert "&lt;script&gt;" in html
    assert "&amp;" in html
    assert "<script>" not in html


def test_a_code_span_escapes_too():
    assert fmt.render("`<b>&`") == "<code>&lt;b&gt;&amp;</code>"


def test_a_long_answer_is_cut_inside_the_limit():
    text = "\n\n".join("Абзац " + str(i) + ". " + "слово " * 400 for i in range(8))
    parts = fmt.parts(text)
    assert len(parts) > 1
    assert all(len(part.html) <= fmt.LIMIT - fmt.RESERVE for part in parts)


def test_a_pre_block_is_never_torn_between_messages():
    code = "\n".join(f"строка {i} = {i}" for i in range(800))
    parts = fmt.parts("Вступление\n\n```\n" + code + "\n```")
    assert 1 < len(parts) <= fmt.MAX_PARTS
    for part in parts:
        assert part.html.count("<pre>") == part.html.count("</pre>")
    blocks = [part for part in parts if "<pre>" in part.html]
    assert blocks[0].html.startswith("<pre>строка 0 = 0\n")
    assert blocks[-1].html.endswith("строка 799 = 799</pre>")


def test_six_messages_at_most_and_a_note_about_the_rest():
    text = "\n\n".join("Абзац " + str(i) + ". " + "слово " * 500 for i in range(12))
    parts = fmt.parts(text, lang="ru")
    assert len(parts) == fmt.MAX_PARTS
    assert "ещё" in parts[-1].html and "частей" in parts[-1].html


def test_an_empty_answer_sends_nothing():
    assert fmt.parts("") == []
    assert fmt.parts("   \n\n \n") == []
    assert fmt.render("") == ""


def test_a_part_carries_its_plain_words_for_a_refused_parse():
    parts = fmt.parts("**жирный** текст")
    assert parts[0].html == "<b>жирный</b> текст"
    assert parts[0].text == "**жирный** текст"
    assert fmt.plain(parts[0].html) == "жирный текст"

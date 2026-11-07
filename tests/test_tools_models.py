from nanomuse.tools.models import _audio_kind, _kind, _money, _open


def _row(**extra):
    row = {
        "id": "image-lite",
        "name": "Image Lite",
        "kind": "image",
        "available": True,
        "plan_locked": False,
        "commerce": {
            "unit": "image",
            "effective_rub": {"image": 3.61, "image_2k": 7.22},
            "rates_rub": {"image": 3.99, "image_2k": 7.98},
        },
    }
    row.update(extra)
    return row


def test_the_account_menu_keeps_kind_and_price_at_the_top():
    row = _row()
    assert _kind(row) == "image"
    assert _money(row) == "₽3.61 за картинку, 2K ₽7.22"


def test_an_openai_entry_keeps_the_same_facts_under_nanomuse():
    row = {
        "id": "openai-gpt-5",
        "object": "model",
        "nanomuse": {
            "kind": "chat",
            "commerce": {"effective_rub": {"input": 10, "output": 30}},
        },
    }
    assert _kind(row) == "chat"
    assert _money(row) == "вход ₽10.00 / выход ₽30.00 за 1M токенов"


def test_a_locked_or_switched_off_model_is_not_offered():
    assert _open(_row()) is True
    assert _open(_row(available=False)) is False
    assert _open(_row(plan_locked=True)) is False


def test_audio_rows_name_their_own_kind():
    music = _row(kind="audio", audio={"kind": "music"}, commerce={"effective_rub": {"track": 12}})
    assert _audio_kind(music) == "music"
    assert _money(music) == "₽12.00 за трек"
    speech = _row(
        kind="audio", audio={"kind": "speech"}, commerce={"effective_rub": {"speech": 1.5}}
    )
    assert _money(speech) == "₽1.50 за озвучку"

from nanomuse.channels.approval_choices import choices


def test_warning_only_once_does_not_offer_persistent_permission():
    assert [scope for code, label, scope in choices(["once"], "ru")] == ["once", "once"]


def test_scopes_are_explicit_and_unknown_scopes_not_exposed():
    result = choices(["once", "conversation", "always", "all"], "ru")
    assert [x[2] for x in result] == ["once", "conversation", "always", "once"]
    assert result[2][1] == "Всегда для этого действия"

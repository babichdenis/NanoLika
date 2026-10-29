from nanomuse.channels.approval_copy import approval_details, readable_approval


def test_shell_card_keeps_plain_task_and_moves_code_to_details():
    event = {
        "tool": "device_shell",
        "purpose": "Проверить сборку сайта",
        "summary": "on host: cd /project && npm run build",
        "args": {"command": "cd /project && npm run build", "device": "Computer"},
    }
    text = readable_approval(event, "Лика")
    assert "Проверить сборку сайта" in text
    assert "сборка или проверка кода" in text
    assert "cd /project" not in text
    assert "может менять файлы" in text
    details = approval_details(event)
    assert "cd /project && npm run build" in details
    assert "Разрешение ещё не выдано" in details


def test_unknown_tool_does_not_get_invented_safe_description():
    text = readable_approval({"tool": "future-tool", "warnings": ["sensitive"]}, "Лика")
    assert "подключённого инструмента" in text
    assert "дополнительные риски" in text


def test_credential_values_hidden_from_card_and_details():
    event = {
        "tool": "shell",
        "purpose": "API_KEY=fixture-secret-value",
        "args": {
            "command": "TOKEN=fixture-secret-value curl -H 'Authorization: Bearer fixture-bearer-value' https://example.test"
        },
    }
    for text in (readable_approval(event, "Лика"), approval_details(event)):
        assert "fixture-secret-value" not in text
        assert "fixture-bearer-value" not in text


def test_missing_args_and_long_commands_have_bounded_details():
    assert len(approval_details({"tool": "shell", "args": {"command": "x " * 10000}})) <= 3900
    assert "Что сделать" in readable_approval({"args": None}, "Лика")


def test_malformed_approval_args_are_treated_as_empty():
    for args in (None, [], ["bad"], "bad", 0):
        event = {"tool": "shell", "args": args}
        assert "Что сделать" in readable_approval(event, "Лика")
        assert "Технические подробности" in approval_details(event)

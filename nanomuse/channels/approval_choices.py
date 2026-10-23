"""Only expose scopes the pending approval explicitly offers."""


def choices(scopes: list[str], lang: str = "") -> list[tuple[str, str, str]]:
    labels = {
        "ru": ("Разрешить один раз", "Для этой беседы", "Всегда для этого действия", "Отказать"),
        "en": ("Allow once", "For this conversation", "Always for this action", "Deny"),
        "zh": ("仅此一次", "此对话", "始终允许此操作", "拒绝"),
    }
    once, conversation, always, deny = labels.get(lang, labels["en"])
    out = [("y", once, "once")]
    if "conversation" in scopes:
        out.append(("c", conversation, "conversation"))
    if "always" in scopes:
        out.append(("p", always, "always"))
    out.append(("n", deny, "once"))
    return out

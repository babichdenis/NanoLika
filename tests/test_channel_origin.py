from nanomuse.channels.origin import conversation_origin


def test_messenger_threads_keep_their_own_origin():
    assert conversation_origin("channel-max-490862977", "MAX · nanoLika") == "max"
    assert conversation_origin("t_any", "MAX · old chat") == "max"
    assert conversation_origin("channel-telegram-224008722", "Telegram · nanoLika") == "telegram"
    assert conversation_origin("t_any", "Telegram · old chat") == "telegram"


def test_account_chats_belong_to_both_messengers():
    assert conversation_origin("main", "Main chat") == "account"
    assert conversation_origin("t_df4a5df7", "Исправление ошибок") == "account"
    assert conversation_origin("t_0e949793", "Лента") == "account"


def test_each_messenger_shows_its_own_chats_and_the_accounts():
    from nanomuse.channels.origin import shows_in

    assert shows_in("max", "max") and not shows_in("max", "telegram")
    assert shows_in("telegram", "telegram") and not shows_in("telegram", "max")
    assert shows_in("account", "telegram") and shows_in("account", "max")

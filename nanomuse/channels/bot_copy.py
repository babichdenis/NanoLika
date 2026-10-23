"""Human channel replies without model calls."""

TEXT = {
    "en": {
        "max_start": "Hi, I am Lika 🌿\n\nI can help with everyday tasks: make a list, draft a letter, explain something in plain language or explore an idea.\n\nTry:\n“Help me make a weekly shopping list”\n“Draft a polite reply to this email”\n“Explain this instruction”\n\nTell me what you need. Chat does not require the desktop app.\n\nMAX currently supports text messages. Please describe your task in words.",
        "help": "Describe your task in your own words.\n/start: introduction and examples\n/status: computer connection\n\nComputer files and screen actions need a separate connection and permissions. MAX currently supports text.\n/computer task: run on the computer",
        "online": "The selected computer is online. Computer tasks still depend on its permissions and availability.",
        "offline": "I am available in chat. Use /computer followed by a task to ask the computer. Lika must be open there with the same account. Ordinary chat works without the app.",
        "welcome": "Hi, I am Lika 🌿\n\nI can help with everyday tasks: make a list, draft a letter, explain something in plain language or explore an idea.\n\nTry:\n“Help me make a weekly shopping list”\n“Draft a polite reply to this email”\n“Explain this instruction”\n\nTell me what you need. Chat does not require the desktop app.",
        "hello": "Hi, I am Lika 🌿\nWhat would you like help with? We can make a list, draft a text or work through a question.",
        "limits": "MAX currently supports text messages. Please describe your task in words.",
        "computer_usage": "Add a task after the command, for example:\n/computer Reply: the computer connection works.",
        "computer_unavailable": "I cannot identify one available computer for this task. Open Lika there with the same account. If several computers are available, select one first. Ordinary chat does not require a computer.",
        "computer_started": "Passing this task to the computer. Important actions require permission.",
        "computer_failed": "The computer task could not be completed. I did not repeat it on the server. Check the app and whether any part ran before retrying.",
    },
    "zh": {
        "max_start": "你好，我是 Lika 🌿\n\n我可以帮你整理清单、起草邮件、解释难懂的内容或一起想办法。\n\n可以这样开始：\n“帮我列一份本周购物清单”\n“帮我礼貌地回复这封邮件”\n“解释一下这段说明”\n\n直接告诉我需要什么。聊天不需要安装电脑应用。\n\nMAX 目前支持文字消息，请用文字描述你的任务。",
        "help": "直接描述你的任务。\n/start：介绍和示例\n/status：电脑连接状态\n\n电脑文件和屏幕操作需要单独连接并授权。MAX 目前支持文字消息。\n/computer 任务：交给电脑执行",
        "online": "已选电脑在线。电脑任务仍取决于权限和设备是否可用。",
        "offline": "我可以在这里聊天。用 /computer 加任务内容请求电脑处理，电脑需打开 Lika 并登录同一账号。普通聊天不需要应用。",
        "welcome": "你好，我是 Lika 🌿\n\n我可以帮你整理清单、起草邮件、解释难懂的内容或一起想办法。\n\n可以这样开始：\n“帮我列一份本周购物清单”\n“帮我礼貌地回复这封邮件”\n“解释一下这段说明”\n\n直接告诉我需要什么。聊天不需要安装电脑应用。",
        "hello": "你好，我是 Lika 🌿\n想处理什么事？我们可以整理清单、起草文字或一起理清问题。",
        "limits": "MAX 目前支持文字消息，请用文字描述你的任务。",
        "computer_usage": "请在命令后写明任务，例如：\n/computer 回复：电脑连接正常。",
        "computer_unavailable": "目前无法确定唯一可用的电脑。请在目标电脑打开 Lika 并登录同一账号。有多台电脑时需先选择目标。普通聊天不需要电脑。",
        "computer_started": "正在将任务交给电脑。重要操作需要授权。",
        "computer_failed": "电脑任务未能完成，我没有在服务器上重复执行。重试前请检查应用，并确认是否已经执行了部分操作。",
    },
    "ru": {
        "max_start": "Привет, я Лика 🌿\n\nПомогу разобраться с повседневными делами: составить список, подготовить письмо, объяснить сложное простыми словами или придумать идею.\n\nМожно начать так:\n«Помоги составить список покупок на неделю»\n«Подготовь вежливый ответ на письмо»\n«Объясни этот пункт инструкции»\n\nНапиши свою первую просьбу. Для разговора приложение на компьютере не нужно.\n\nВ MAX пока доступны текстовые сообщения. Можно описать задачу словами.",
        "help": "Напиши задачу обычными словами.\n/start — знакомство и примеры\n/status — доступность связи с компьютером\n\nРабота с файлами и экраном компьютера требует отдельного подключения и разрешений. В MAX пока доступны текстовые сообщения.\n/computer поручение — выполнить на компьютере",
        "online": "Выбранный компьютер в сети. Выполнение поручений зависит от его разрешений и доступности.",
        "offline": "Я на связи в чате. Для проверки задачи на компьютере используй /computer и текст поручения. Нужны открытая Лика на компьютере и тот же аккаунт. Обычный разговор работает без приложения.",
        "welcome": "Привет, я Лика 🌿\n\nПомогу разобраться с повседневными делами: составить список, подготовить письмо, объяснить сложное простыми словами или придумать идею.\n\nМожно начать так:\n«Помоги составить список покупок на неделю»\n«Подготовь вежливый ответ на письмо»\n«Объясни этот пункт инструкции»\n\nНапиши свою первую просьбу. Для разговора приложение на компьютере не нужно.",
        "hello": "Привет, я Лика 🌿\nС чем помочь? Можем составить список, подготовить текст или разобраться в вопросе.",
        "limits": "В MAX пока доступны текстовые сообщения. Можно описать задачу словами.",
        "computer_usage": "Напиши поручение после команды, например:\n/computer Ответь: связь с компьютером работает.",
        "computer_unavailable": "Не вижу одного доступного компьютера для этого поручения. Открой Лику на нужном компьютере с тем же аккаунтом. Если компьютеров несколько, сначала нужно выбрать, какому передавать задачу. В обычном чате я могу помогать и без компьютера.",
        "computer_started": "Передаю поручение компьютеру. Важные действия потребуют разрешения.",
        "computer_failed": "Не удалось завершить поручение на компьютере. Я не стала повторять его на сервере. Проверь приложение; перед повтором уточни, не выполнилась ли часть задачи.",
    },
}


def channel_text(language: str, key: str) -> str:
    language = language.lower()
    locale = "ru" if language.startswith("ru") else "zh" if language.startswith("zh") else "en"
    return TEXT[locale][key]

"""How a code reaches the person: the log (development), SMTP, an HTTPS mail endpoint
(the site sends it, for a relay whose host closes the SMTP ports), or Aliyun SMS.

`send(identifier, code)` raises `SendError` when the message could not go out
so the API can say so instead of letting the user wait for nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import smtplib
import time
import urllib.parse
import uuid
from email.message import EmailMessage
from typing import Protocol

import httpx

from .config import Settings
from .identifiers import Identifier

log = logging.getLogger("nanomuse_cloud.send")


class SendError(RuntimeError):
    pass


class CodeSender(Protocol):
    def send(self, ident: Identifier, code: str) -> None: ...

    def accepts(self, ident: Identifier) -> bool:
        """Can a code reach this identifier at all? Asked before one is made, so the
        answer is a plain 400 and not a failed send."""
        ...


class LogSender:
    """Development: the code goes to the log, and stays reachable for tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[Identifier, str]] = []

    def accepts(self, ident: Identifier) -> bool:
        return True

    def send(self, ident: Identifier, code: str) -> None:
        self.sent.append((ident, code))
        log.warning("verification code for %s (%s): %s", ident.hint, ident.channel, code)


class SmtpSender:
    def __init__(self, s: Settings) -> None:
        if not (s.smtp_host and s.smtp_from):
            raise ValueError("SMTP_HOST and SMTP_FROM are required for CODE_SENDER=smtp")
        self.s = s

    def accepts(self, ident: Identifier) -> bool:
        return ident.channel == "email"

    def send(self, ident: Identifier, code: str) -> None:
        if ident.channel != "email":
            raise SendError("this deployment sends codes by e-mail only")
        # the same rounding as the SMS template: a lifetime under a minute still reads as one
        msg = compose_code_mail(self.s.smtp_from, ident.value, code, max(1, self.s.code_ttl_s // 60))
        smtp_send(self.s, msg)

    def send_first_login(self, ident: Identifier) -> None:
        if ident.channel == "email":
            smtp_send(self.s, compose_first_login_mail(self.s.smtp_from, ident.value))

    def send_channel_link(self, ident: Identifier, channel: str, who: str, when: str) -> None:
        """A chat has joined this account: tell its owner, so a link nobody asked for is seen.

        Rare by nature (a person links a messenger once), and the only warning there is: the
        app lists connected chats, but nobody opens that list looking for a surprise."""
        if ident.channel != "email":
            return
        msg = EmailMessage()
        msg["From"] = self.s.smtp_from if "<" in self.s.smtp_from else f"NanoLika <{self.s.smtp_from}>"
        msg["To"] = ident.value
        msg["Subject"] = channel_link_mail_subject(channel)
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Auto-Response-Suppress"] = "All"
        msg.set_content(channel_link_mail_text(channel, who, when))
        msg.add_alternative(channel_link_mail_html(channel, who, when), subtype="html")
        smtp_send(self.s, msg)


def smtp_send(s: Settings, msg: EmailMessage) -> None:
    """One message out through the relay's SMTP settings; SendError when it did not go."""
    if not (s.smtp_host and s.smtp_from):
        raise SendError("mail: SMTP_HOST and SMTP_FROM are not set")
    try:
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            server.starttls()
        with server:
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("smtp send failed: %s", smtp_error_text(e))
        raise SendError("mail") from e


def smtp_error_text(e: BaseException) -> str:
    """What the log may say about a failed send: the error's kind and the server's code and
    words, never the recipient. ``str()`` of an SMTPRecipientsRefused is a dict keyed by the
    addresses refused, and SMTPSenderRefused carries the From address."""
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        codes = sorted({str(v[0]) for v in e.recipients.values()} if isinstance(e.recipients, dict) else set())
        return f"SMTPRecipientsRefused ({len(e.recipients)} recipient(s); {', '.join(codes) or 'no code'})"
    if isinstance(e, smtplib.SMTPResponseException):
        error = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return f"{type(e).__name__} {e.smtp_code} {error[:120]}"
    return f"{type(e).__name__}: {str(e)[:160]}"


class WebhookMailSender:
    """Mail through the site's own HTTPS endpoint.

    A relay whose host closes the outbound SMTP ports cannot talk to a mail server at all
    (reg.ru closes 25, 465 and 587 to addresses outside Russia), so the message is handed to
    a page that runs on a Russian host: it builds the MIME body, signs it with DKIM and puts
    it in the local queue, where SPF and the relay's IP no longer matter. The relay posts
    JSON and an HMAC of the exact bytes, so the endpoint is not an open relay and a bad
    signature never reaches the mail server."""

    def __init__(self, s: Settings) -> None:
        if not (s.mail_webhook_url and s.mail_webhook_secret):
            raise ValueError("MAIL_WEBHOOK_URL and MAIL_WEBHOOK_SECRET are required for CODE_SENDER=webhook")
        self.s = s

    def accepts(self, ident: Identifier) -> bool:
        return ident.channel == "email"

    def send(self, ident: Identifier, code: str) -> None:
        if ident.channel != "email":
            raise SendError("this deployment sends codes by e-mail only")
        minutes = max(1, self.s.code_ttl_s // 60)
        webhook_send(
            self.s,
            ident.value,
            code_mail_subject(code),
            code_mail_text(code, minutes),
            # the letter the person actually opens: the plain text above stays as the fallback,
            # and the code is never in the subject (lock-screen previews)
            html=_brand_email_html(
                preheader=f"Код для входа в nanoLika, действует {minutes} мин.",
                title="Ваш код для входа",
                paragraphs=[
                    "Здравствуйте! Введите этот код в nanoLika — и вы на месте.",
                    "Код одноразовый: никому его не пересылайте. Если вы не просили код, просто удалите письмо — без него никто не войдёт.",
                ],
                code=code,
                minutes=minutes,
                action_label="Открыть nanoLika",
                action_url="https://cloud.nanolika.ru/app/",
            ),
        )

    def send_first_login(self, ident: Identifier) -> None:
        if ident.channel == "email":
            webhook_send(
                self.s,
                ident.value,
                "nanoLika · рады знакомству",
                "Добро пожаловать в nanoLika!\n\n"
                "Рады, что вы с нами. Начните с небольшой понятной просьбы — например, "
                "попросите Лику помочь с письмом или найти нужную информацию.\n"
                "Вы сами выбираете доступы и подтверждаете важные действия.\n\n"
                "Идеи для первой просьбы: https://nanolika.ru/tips.html\n"
                "nanoLika · https://nanolika.ru/",
                html=_brand_email_html(
                    preheader="Рады, что вы с нами: nanoLika",
                    title="Рады знакомству!",
                    paragraphs=[
                        "Здравствуйте! Лика уже рядом: на компьютере, в телефоне и в браузере.",
                        "Начните с небольшой понятной просьбы — помочь с письмом, найти нужную "
                        "страницу, разобрать документ. Доступы вы выбираете сами, а важные "
                        "действия Лика спрашивает перед тем, как сделать.",
                    ],
                    action_label="Идеи для первой просьбы",
                    action_url="https://nanolika.ru/tips.html",
                ),
            )

    def send_channel_link(self, ident: Identifier, channel: str, who: str, when: str) -> None:
        """The same letter as the SMTP sender's, out through the relay's mail webhook."""
        if ident.channel != "email":
            return
        webhook_send(
            self.s,
            ident.value,
            channel_link_mail_subject(channel),
            channel_link_mail_text(channel, who, when),
            html=channel_link_mail_html(channel, who, when),
        )


def channel_link_mail_text(channel: str, who: str, when: str) -> str:
    """The words of the "a chat joined your account" letter, text first.

    Written for the person who owns the account and did not do this: the first line says what
    happened, the second what to do about it. Nothing here needs the letter to be understood
    twice, so the Russian comes first and the English follows, as the code letter does."""
    messenger = "Telegram" if channel == "telegram" else "MAX"
    return (
        f"К вашему аккаунту nanoLika подключён чат в {messenger}.\n\n"
        f"Кто: {who or 'имя не показано'}\n"
        f"Когда: {when}\n\n"
        "С этого момента этот чат работает с вашим аккаунтом: тратит вашу кассу, видит беседы, "
        "которые хранятся в облаке, и может отдавать поручения вашим устройствам.\n\n"
        "Если это были вы, ничего делать не нужно.\n"
        "Если нет: откройте приложение, раздел «Аккаунт» → «Устройства и каналы», и отвяжите этот чат. "
        "Он перестанет быть вами сразу же.\n\n"
        f"A {messenger} chat has been linked to your nanoLika account.\n"
        f"Who: {who or 'no name shown'}\n"
        f"When: {when}\n\n"
        "If this was you, nothing needs doing. If it was not, open the app, Account, Devices and "
        "channels, and unlink that chat.\n\n"
        "nanoLika · https://nanolika.ru/"
    )


def channel_link_mail_subject(channel: str) -> str:
    messenger = "Telegram" if channel == "telegram" else "MAX"
    return f"nanoLika · к аккаунту подключён чат в {messenger}"


def channel_link_mail_html(channel: str, who: str, when: str) -> str:
    messenger = "Telegram" if channel == "telegram" else "MAX"
    return _brand_email_html(
        preheader=f"Чат в {messenger} подключён к вашему аккаунту nanoLika",
        title=f"Чат в {messenger} подключён",
        paragraphs=[
            f"К вашему аккаунту nanoLika подключён чат в {messenger}.",
            f"Кто: {who or 'имя не показано'}. Когда: {when}.",
            "Этот чат теперь работает с вашим аккаунтом: тратит вашу кассу, видит беседы из "
            "облака и может отдавать поручения вашим устройствам.",
            "Если это были вы, ничего делать не нужно. Если нет, отвяжите чат в приложении: "
            "«Аккаунт» → «Устройства и каналы». Он перестанет быть вами сразу же.",
        ],
        action_label="Устройства и каналы",
        action_url="https://cloud.nanolika.ru/web/account/#devices",
        safety_note="Это автоматическое письмо nanoLika о подключении чата к аккаунту.",
    )


def webhook_send(s: Settings, to: str, subject: str, text: str, html: str | None = None) -> None:
    """One message through MAIL_WEBHOOK_URL; SendError when it did not go.

    ``html`` is the same letter in a warm layout; the site's page builds
    multipart/alternative from the two. A site that only knows ``text`` ignores it, so the plain
    text has to stand on its own."""
    if not (s.mail_webhook_url and s.mail_webhook_secret):
        raise SendError("mail: MAIL_WEBHOOK_URL and MAIL_WEBHOOK_SECRET are not set")
    body: dict[str, object] = {"to": to, "subject": subject, "text": text, "ts": int(time.time())}
    if html:
        body["html"] = html
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    signature = hmac.new(s.mail_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    try:
        r = httpx.post(
            s.mail_webhook_url,
            content=payload,
            headers={"Content-Type": "application/json", "X-Nanolika-Signature": signature},
            timeout=20,
        )
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.error("mail webhook request failed: %s: %s", type(e).__name__, str(e)[:160])
        raise SendError("mail") from e
    if r.status_code != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        # the endpoint's own words ("not configured", "rate", "sender") are the operator's to read
        reason = body.get("error") if isinstance(body, dict) else None
        log.error("mail webhook refused: %s %s", r.status_code, reason)
        raise SendError("mail")


def notice_mail_subject(subject: str) -> str:
    return f"NanoLika Cloud · {subject}"


def notice_mail_text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n\nNanoLika · автоматическое письмо, отвечать не нужно / automated, no reply\n"


def compose_notice_mail(sender: str, to: str, subject: str, lines: list[str]) -> EmailMessage:
    """A plain note to the operator (0.22: a threshold rule fired). Text only, both
    languages' worth of facts in the lines the caller gives; nothing personal in it."""
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"NanoLika <{sender}>"
    msg["To"] = to
    msg["Subject"] = notice_mail_subject(subject)
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(notice_mail_text(lines))
    return msg


def code_mail_subject(code: str) -> str:
    "Keep one-time codes out of lock-screen previews and subject listings."
    del code
    return "nanoLika · ваш код для входа"


def code_mail_text(code: str, minutes: int) -> str:
    "Friendly Russian-first copy; a verified email code can also restore access."
    return (
        f"Здравствуйте! Вот ваш код для nanoLika: {code}.\n"
        f"Он действует {minutes} мин. Войдите по коду, а затем задайте новый пароль в настройках аккаунта, если забыли старый.\n"
        "Никому не пересылайте код.\n\n"
        f"Your sign-in code is {code}; it expires in {minutes} minutes. Sign in with it, then set a new password in account settings if needed. Do not forward it.\n\n"
        "Если вы не запрашивали его, просто удалите это письмо.\n"
        "If you did not ask for it, you can ignore this message.\n\n"
        "nanoLika · https://nanolika.ru/"
    )


def _brand_email_html(
    *,
    preheader: str,
    title: str,
    paragraphs: list[str],
    code: str | None = None,
    minutes: int | None = None,
    action_label: str | None = None,
    action_url: str | None = None,
    safety_note: str = "Это автоматическое письмо nanoLika. Если оно не относится к вам, проигнорируйте его.",
) -> str:
    "Table-based paper-and-sage layout with a hosted Lika image and a text fallback."
    esc = html.escape
    text_rows = "".join(
        '<tr><td style="padding:0 0 12px;font:15px/1.65 -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#3e463c">'
        + esc(line)
        + "</td></tr>"
        for line in paragraphs
    )
    code_panel = ""
    if code is not None:
        # non-breaking spaces: a phone that is narrow enough to wrap "4 8 2 9 1 3" must not put
        # the last digit on its own line — the code is read once and copied by hand
        spaced = "&nbsp;".join(esc(digit) for digit in code)
        lifetime = f"Действует {minutes} мин. · Valid for {minutes} minutes" if minutes is not None else ""
        code_panel = f"""<tr><td style="padding:8px 0 18px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f2e9;border:1px solid #d7ddcf;border-radius:14px"><tr><td class="nm-code" align="center" style="padding:19px 12px 10px;font:600 27px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:2px;color:#34483b;font-variant-numeric:tabular-nums;white-space:nowrap;word-break:keep-all;overflow-wrap:normal">{spaced}</td></tr><tr><td align="center" style="padding:0 12px 17px;font:13px/1.45 -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#667260">{esc(lifetime)}</td></tr></table>
</td></tr>"""
    action = ""
    if action_label and action_url:
        parsed = urllib.parse.urlparse(action_url)
        if parsed.scheme != "https" or parsed.hostname not in {"nanolika.ru", "cloud.nanolika.ru"}:
            raise ValueError("customer email links must use an HTTPS NanoLika address")
        action = f'''<tr><td align="center" style="padding:4px 0 18px"><a href="{esc(action_url, quote=True)}" style="display:inline-block;padding:12px 20px;border-radius:10px;background:#536b55;color:#fffdf8;text-decoration:none;font:600 14px/1.3 -apple-system,BlinkMacSystemFont,Arial,sans-serif">{esc(action_label)}</a></td></tr>'''
    safe_preheader = esc(preheader)
    safe_safety_note = esc(safety_note)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>@media only screen and (max-width:420px){{.nm-code{{font-size:27px!important;letter-spacing:2px!important}}}}</style></head>
<body style="margin:0;padding:0;background:#f5f1e7;color:#303a32">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{safe_preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f1e7"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px"><tr><td style="padding:0 8px 12px;font:600 18px/1.3 Georgia,'Times New Roman',serif;letter-spacing:.2px;color:#405444"><span style="color:#c58a42">✦</span> nano<b style="color:#536b55">Lika</b></td></tr>
<tr><td style="background:#fffdf8;border:1px solid #dedfce;border-radius:12px;padding:22px 26px 24px;box-shadow:0 8px 24px rgba(65,75,57,.07)">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td width="76" valign="middle" style="padding:0 16px 10px 0"><img src="https://nanolika.ru/assets/lika/idle.webp" width="64" height="64" alt="Лика, помощница nanoLika" style="display:block;width:64px;height:64px;border-radius:50%;border:1px solid #d7ddcf;object-fit:cover"></td><td valign="middle" style="padding:0 0 10px;font:14px/1.5 -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#667260">Тёплый привет от Лики</td></tr></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:0 0 8px;font:700 23px/1.3 Georgia,'Times New Roman',serif;color:#303a32">{esc(title)}</td></tr>
{text_rows}{code_panel}{action}
<tr><td style="padding:10px 0 0;border-top:1px solid #e5e4d8;font:13px/1.6 -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#74796e">{safe_safety_note}</td></tr></table>
</td></tr>
<tr><td style="padding:15px 8px 0;font:12px/1.6 -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#787d72">Автоматическое письмо nanoLika · <a href="https://nanolika.ru" style="color:#536b55">nanolika.ru</a> · <a href="https://nanolika.ru/privacy.html" style="color:#536b55">Конфиденциальность</a></td></tr>
</table></td></tr></table></body></html>"""


def compose_customer_notice_mail(
    sender: str,
    to: str,
    subject: str,
    title: str,
    paragraphs: list[str],
    *,
    action_label: str | None = None,
    action_url: str | None = None,
    reply_allowed: bool = False,
) -> EmailMessage:
    "Reusable branded template for a factual service notice; callers supply approved copy."
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"NanoLika <{sender}>"
    msg["To"] = to
    msg["Subject"] = f"NanoLika · {subject}"
    if not reply_allowed:
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Auto-Response-Suppress"] = "All"
    plain = "\n\n".join([title, *paragraphs])
    if action_label and action_url:
        plain += f"\n\n{action_label}: {action_url}"
    plain += (
        "\n\nnanoLika · https://nanolika.ru/ · можно ответить на это письмо\n"
        if reply_allowed
        else "\n\nnanoLika · https://nanolika.ru/ · автоматическое письмо\n"
    )
    msg.set_content(plain)
    msg.add_alternative(
        _brand_email_html(
            preheader=paragraphs[0] if paragraphs else title,
            title=title,
            paragraphs=paragraphs,
            action_label=action_label,
            action_url=action_url,
            safety_note=(
                "Ответьте на это письмо, если захотите уточнить детали."
                if reply_allowed
                else "Это автоматическое письмо nanoLika. Если оно не относится к вам, проигнорируйте его."
            ),
        ),
        subtype="html",
    )
    return msg


def compose_code_mail(sender: str, to: str, code: str, minutes: int) -> EmailMessage:
    "Code mail with a large readable code and a plain-text fallback."
    msg = EmailMessage()
    msg["From"] = sender if "<" in sender else f"NanoLika <{sender}>"
    msg["To"] = to
    msg["Subject"] = code_mail_subject(code)
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(code_mail_text(code, minutes))
    msg.add_alternative(
        _brand_email_html(
            preheader=f"Код для входа в NanoLika. Действует {minutes} минут.",
            title="Ваш код для nanoLika",
            paragraphs=[
                "Войдите по коду, а затем задайте новый пароль в настройках аккаунта, если забыли старый. Никому не пересылайте код.",
                "Sign in with it, then set a new password in account settings if needed. Please do not forward it.",
            ],
            code=code,
            minutes=minutes,
            safety_note="Если вы не запрашивали код, удалите письмо. Никому не сообщайте код и не пересылайте его.",
        ),
        subtype="html",
    )
    return msg


def compose_first_login_mail(sender: str, to: str) -> EmailMessage:
    "A short welcome for a newly created email account."
    return compose_customer_notice_mail(
        sender,
        to,
        "рады знакомству",
        "Добро пожаловать в nanoLika!",
        [
            "Рады, что вы с нами. Начните с небольшой понятной просьбы — например, попросите Лику помочь с письмом или найти нужную информацию.",
            "Вы сами выбираете доступы и подтверждаете важные действия.",
        ],
        action_label="Идеи для первой просьбы",
        action_url="https://nanolika.ru/tips.html",
    )


def compose_topup_thanks_mail(sender: str, to: str, amount: str, *, receipt_note: str = "") -> EmailMessage:
    "Template for a confirmed live top-up; never sent for the isolated DEMO wallet."
    paragraphs = [
        f"Пополнение на {amount} ₽ подтверждено. Спасибо, что выбрали nanoLika!",
        "Баланс можно посмотреть в приложении или в веб-версии.",
    ]
    if receipt_note:
        paragraphs.append(receipt_note)
    return compose_customer_notice_mail(
        sender,
        to,
        "пополнение подтверждено",
        "Спасибо за пополнение!",
        paragraphs,
        action_label="Открыть nanoLika",
        action_url="https://cloud.nanolika.ru/web/",
    )


def compose_password_changed_mail(sender: str, to: str) -> EmailMessage:
    "Security notice template; the password itself is never included."
    return compose_customer_notice_mail(
        sender,
        to,
        "пароль изменён",
        "Пароль nanoLika изменён",
        ["Если это сделали вы, ничего делать не нужно.", "Если вы не меняли пароль, напишите нам: hello@nanolika.ru."],
        action_label="Связаться с поддержкой",
        action_url="https://nanolika.ru/support.html",
    )


def compose_payment_failed_mail(sender: str, to: str) -> EmailMessage:
    "Template for a bank-confirmed unsuccessful payment; no charge claim before status check."
    return compose_customer_notice_mail(
        sender,
        to,
        "оплата не завершилась",
        "Не получилось завершить оплату",
        [
            "Банк не подтвердил операцию. Перед новой попыткой проверьте статус заказа в nanoLika.",
            "Если сумма списалась, а баланс не изменился, напишите в поддержку и укажите время и сумму. Данные карты присылать не нужно.",
        ],
        action_label="Написать в поддержку",
        action_url="https://nanolika.ru/support.html",
    )


def compose_refund_notice_mail(sender: str, to: str, amount: str) -> EmailMessage:
    "Template for a bank-confirmed refund; the caller must supply the reconciled amount."
    return compose_customer_notice_mail(
        sender,
        to,
        "возврат подтверждён",
        "Возврат подтверждён",
        [
            f"Банк подтвердил возврат на {amount} ₽. Срок зачисления зависит от банка получателя.",
            "Если у вас остались вопросы, напишите в поддержку.",
        ],
        action_label="Написать в поддержку",
        action_url="https://nanolika.ru/support.html",
    )


def compose_support_received_mail(sender: str, to: str, ticket_ref: str = "") -> EmailMessage:
    "Template for a future server-received support request; do not send from the mailto form."
    paragraphs = ["Мы получили ваше письмо и вернёмся с ответом на этот адрес."]
    if ticket_ref:
        paragraphs.append(f"Номер обращения: {ticket_ref}.")
    return compose_customer_notice_mail(sender, to, "мы получили ваше письмо", "Спасибо, письмо дошло!", paragraphs)


def compose_support_reply_mail(sender: str, to: str, reply: str, *, topic: str = "ваш вопрос") -> EmailMessage:
    "A human support reply that keeps the reply path open."
    msg = compose_customer_notice_mail(
        sender,
        to,
        "ответ поддержки",
        "Мы разобрались с вашим вопросом",
        [f"По теме «{topic}»:", reply],
        reply_allowed=True,
    )
    del msg["Subject"]
    msg["Subject"] = f"nanoLika · ответ поддержки: {topic}"
    return msg


def _pct(v: str) -> str:
    return urllib.parse.quote(v, safe="~")


def aliyun_common_params(access_key_id: str) -> dict[str, str]:
    """What every RPC-style Aliyun call (dypnsapi, dysmsapi; API version 2017-05-25) carries."""
    return {
        "AccessKeyId": access_key_id,
        "Format": "JSON",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2017-05-25",
    }


def aliyun_signed_query(access_key_secret: str, params: dict[str, str]) -> str:
    """The query string of a GET, signed the RPC way: HMAC-SHA1 over the sorted, encoded pairs."""
    canonical = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    string_to_sign = "GET&%2F&" + _pct(canonical)
    digest = hmac.new((access_key_secret + "&").encode(), string_to_sign.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()
    return canonical + "&Signature=" + _pct(signature)


class AliyunSmsSender:
    """Aliyun SMS, signed the RPC way (HMAC-SHA1), no SDK. Two services:

    - `ALIYUN_SMS_API=dypns` (default): 号码认证服务's `SendSmsVerifyCode`
      (dypnsapi). Its ready-made templates take `code` and `min`; our own code
      is passed in `TemplateParam`, so the relay still verifies it locally.
    - `ALIYUN_SMS_API=dysms`: 短信服务's `SendSms` (dysmsapi) with a template
      whose one variable is `code`, e.g. 「您的验证码为${code}，10分钟内有效。」.

    Mainland numbers are sent without +86; dysms takes others with the country
    code and no plus, dypns only mainland numbers.
    """

    ENDPOINTS = {"dypns": "https://dypnsapi.aliyuncs.com/", "dysms": "https://dysmsapi.aliyuncs.com/"}

    def __init__(self, s: Settings) -> None:
        if not (s.aliyun_access_key_id and s.aliyun_access_key_secret and s.aliyun_sms_sign and s.aliyun_sms_template):
            raise ValueError("ALIYUN_ACCESS_KEY_ID/SECRET, ALIYUN_SMS_SIGN and ALIYUN_SMS_TEMPLATE are required for CODE_SENDER=aliyun")
        if s.aliyun_sms_api not in self.ENDPOINTS:
            raise ValueError("ALIYUN_SMS_API must be dypns or dysms")
        self.s = s

    @property
    def api(self) -> str:
        return self.s.aliyun_sms_api

    @property
    def endpoint(self) -> str:
        return self.ENDPOINTS[self.api]

    def _signed_query(self, params: dict[str, str]) -> str:
        return aliyun_signed_query(self.s.aliyun_access_key_secret, params)

    def accepts(self, ident: Identifier) -> bool:
        """Phones only; 号码认证 (dypns) reaches mainland numbers only, 短信服务 (dysms) any."""
        return ident.channel == "phone" and (self.api == "dysms" or ident.value.startswith("+86"))

    def params(self, ident: Identifier, code: str) -> dict[str, str]:
        """The request for one code, before signing; a mainland number without +86."""
        if ident.channel != "phone":
            raise SendError("this deployment sends codes by SMS only")
        mainland = ident.value.startswith("+86")
        number = ident.value[3:] if mainland else ident.value.lstrip("+")
        minutes = str(max(1, self.s.code_ttl_s // 60))
        common = {
            **aliyun_common_params(self.s.aliyun_access_key_id),
            "SignName": self.s.aliyun_sms_sign,
            "TemplateCode": self.s.aliyun_sms_template,
        }
        if self.api == "dypns":
            if not mainland:
                raise SendError("this deployment sends codes to mainland numbers only")
            return {
                **common,
                "Action": "SendSmsVerifyCode",
                "PhoneNumber": number,
                "CountryCode": "86",
                "TemplateParam": json.dumps({"code": code, "min": minutes}),
                "ValidTime": str(self.s.code_ttl_s),
                "ReturnVerifyCode": "false",
            }
        return {
            **common,
            "Action": "SendSms",
            "PhoneNumbers": number,
            "TemplateParam": json.dumps({"code": code}),
        }

    def send(self, ident: Identifier, code: str) -> None:
        url = self.endpoint + "?" + self._signed_query(self.params(ident, code))
        try:
            r = httpx.get(url, timeout=15)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            # the signed query carries the number and the code; an error that quotes the
            # URL is cut at the question mark before it reaches the log
            log.error("aliyun sms request failed: %s: %s", type(e).__name__, str(e).split("?", 1)[0][:160])
            raise SendError("sms") from e
        if body.get("Code") != "OK":
            # never the number: BUSINESS_LIMIT_CONTROL (too many for one number), FREQUENCY_FAIL,
            # MOBILE_NUMBER_ILLEGAL, FUNCTION_NOT_OPENED, isv.* — all are the operator's to read
            log.error("aliyun sms (%s) rejected: %s %s", self.api, body.get("Code"), body.get("Message"))
            raise SendError("sms")


def make_sender(s: Settings) -> CodeSender:
    if s.sender == "log":
        if s.signup_open and not s.public_base.startswith(("http://127.", "http://localhost")):
            # a relay with a public address and open sign-up that writes the codes to its log:
            # nobody receives a code, and whoever reads the log can sign in as anyone — say so at startup
            log.error(
                "CODE_SENDER=log with SIGNUP_OPEN at %s: verification codes go to this log and nobody receives them. Set CODE_SENDER=smtp, webhook or aliyun.",
                s.public_base,
            )
        return LogSender()
    if s.sender == "smtp":
        return SmtpSender(s)
    if s.sender == "webhook":
        return WebhookMailSender(s)
    if s.sender == "aliyun":
        return AliyunSmsSender(s)
    if s.sender == "both":
        return BothSender(SmtpSender(s), AliyunSmsSender(s))
    raise ValueError(f"unknown CODE_SENDER {s.sender!r}")


class BothSender:
    """SMS for phones, mail for addresses — the normal production setup."""

    def __init__(self, mail: SmtpSender, sms: AliyunSmsSender) -> None:
        self.mail, self.sms = mail, sms

    def _for(self, ident: Identifier) -> SmtpSender | AliyunSmsSender:
        return self.sms if ident.channel == "phone" else self.mail

    def accepts(self, ident: Identifier) -> bool:
        return self._for(ident).accepts(ident)

    def send(self, ident: Identifier, code: str) -> None:
        self._for(ident).send(ident, code)

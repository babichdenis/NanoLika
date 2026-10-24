#!/usr/bin/env python3
"""Вход в веб-Лику через чужие аккаунты: Яндекс, Google, GitHub, Telegram.

Зачем: чтобы не заводить пароль. Провайдер доказывает адрес почты (или личность
в Телеграме), дальше всё как при входе по коду — `Cloud.sign_in` находит или
заводит аккаунт и выдаёт ключ.

Ключи не в коде: клиентские пары читаются из окружения —
CLOUD_OAUTH_YANDEX_ID / _SECRET, CLOUD_OAUTH_GOOGLE_ID / _SECRET,
CLOUD_OAUTH_GITHUB_ID / _SECRET, CLOUD_OAUTH_TELEGRAM_TOKEN (+ _BOT для виджета).
Провайдер без ключей просто не показывается на странице.

Возврат: /v1/auth/oauth/<provider>/callback?code=…&state=…
Страница получает ключ во фрагменте адреса (#sso=…): фрагмент не уходит на
сервер и не попадает в логи прокси.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request

# Request импортируется на уровне модуля: аннотации строковые (from __future__),
# а FastAPI разрешает их по globals модуля, иначе параметр станет query-параметром
from fastapi import Request

log = logging.getLogger("nanomuse_cloud.oauth")

UA = "NanoLika-Cloud/1.0 (+https://nanolika.ru)"
TIMEOUT = 20
STATE_TTL_S = 600

PROVIDERS = {
    "yandex": dict(
        title="Яндекс",
        authorize="https://oauth.yandex.ru/authorize",
        token="https://oauth.yandex.ru/token",
        profile="https://login.yandex.ru/info?format=json",
        scope="login:email login:info",
        id_env="CLOUD_OAUTH_YANDEX_ID", secret_env="CLOUD_OAUTH_YANDEX_SECRET",
    ),
    "google": dict(
        title="Google",
        authorize="https://accounts.google.com/o/oauth2/v2/auth",
        token="https://oauth2.googleapis.com/token",
        profile="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
        id_env="CLOUD_OAUTH_GOOGLE_ID", secret_env="CLOUD_OAUTH_GOOGLE_SECRET",
    ),
    "github": dict(
        title="GitHub",
        authorize="https://github.com/login/oauth/authorize",
        token="https://github.com/login/oauth/access_token",
        profile="https://api.github.com/user",
        scope="read:user user:email",
        id_env="CLOUD_OAUTH_GITHUB_ID", secret_env="CLOUD_OAUTH_GITHUB_SECRET",
    ),
    "telegram": dict(
        title="Telegram",
        widget=True,
        token_env="CLOUD_OAUTH_TELEGRAM_TOKEN",
        bot_env="CLOUD_OAUTH_TELEGRAM_BOT",
    ),
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def provider_state(name: str) -> dict:
    """Что известно о провайдере: включён, название, чем входить."""
    conf = PROVIDERS[name]
    if conf.get("widget"):
        return dict(name=name, title=conf["title"], kind="widget",
                    enabled=bool(_env(conf["token_env"]) and _env(conf["bot_env"])),
                    bot=_env(conf["bot_env"]))
    enabled = bool(_env(conf["id_env"]) and _env(conf["secret_env"]))
    return dict(name=name, title=conf["title"], kind="redirect", enabled=enabled)


def enabled_providers() -> list[dict]:
    return [s for s in (provider_state(n) for n in PROVIDERS) if s["enabled"]]


# ── OAuth-редирект ──────────────────────────────────────────────────────
def authorize_url(provider: str, redirect_uri: str, state: str) -> str:
    conf = PROVIDERS[provider]
    params = {
        "client_id": _env(conf["id_env"]),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": conf["scope"],
        "state": state,
    }
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return conf["authorize"] + "?" + urllib.parse.urlencode(params)


def _post(url: str, data: dict, headers: dict | None = None) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": UA,
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _get(url: str, token: str) -> str:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def fetch_profile(provider: str, code: str, redirect_uri: str) -> dict:
    """Меняет код на профиль. Возвращает {subject, email, name}."""
    conf = PROVIDERS[provider]
    token_body = _post(conf["token"], {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": _env(conf["id_env"]),
        "client_secret": _env(conf["secret_env"]),
        "redirect_uri": redirect_uri,
    }, headers={"Accept": "application/json"})
    try:
        token = json.loads(token_body).get("access_token", "")
    except json.JSONDecodeError:                      # Яндекс умеет form-urlencoded
        token = urllib.parse.parse_qs(token_body).get("access_token", [""])[0]
    if not token:
        raise RuntimeError(f"{provider}: токен не выдан")

    if provider == "github":
        raw = json.loads(_get("https://api.github.com/user", token))
        email = raw.get("email") or ""
        if not email:                                  # приватная почта — спросим отдельно
            mails = json.loads(_get("https://api.github.com/user/emails", token))
            primary = [m for m in mails if m.get("primary") and m.get("verified")]
            email = primary[0]["email"] if primary else ""
        return dict(subject=str(raw.get("id", "")), email=email,
                    name=raw.get("login") or "")

    raw = json.loads(_get(conf["profile"], token))
    if provider == "yandex":
        email = raw.get("default_email") or ""
        if not email:
            emails = raw.get("emails") or []
            email = emails[0] if emails else ""
        return dict(subject=str(raw.get("id", "")), email=email,
                    name=raw.get("login") or raw.get("real_name") or "")
    email = raw.get("email") or ""
    return dict(subject=str(raw.get("sub", "")), email=email,
                name=raw.get("name") or raw.get("given_name") or "")


# ── Telegram Login Widget ───────────────────────────────────────────────
def check_telegram(payload: dict) -> dict | None:
    """Проверяет подпись виджета Телеграма. Возвращает {subject, name} или None."""
    token = _env(PROVIDERS["telegram"]["token_env"])
    if not token:
        return None
    data = {k: v for k, v in payload.items() if k != "hash"}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(payload.get("hash", ""))):
        return None
    try:
        if time.time() - int(data.get("auth_date", "0")) > 86400:
            return None
    except (TypeError, ValueError):
        return None
    return dict(subject=str(data.get("id", "")),
                name=" ".join(x for x in (data.get("first_name"), data.get("last_name")) if x)
                or data.get("username") or "")


# ── ручки ───────────────────────────────────────────────────────────────
def install_oauth(app, cloud, settings) -> None:
    """Три ручки: список провайдеров, старт и возврат. Плюс вход по виджету Telegram."""
    from fastapi import Body
    from fastapi.responses import JSONResponse, RedirectResponse

    from .identifiers import parse as parse_identifier, telegram as telegram_identifier

    base = (getattr(settings, "public_base", "") or "https://cloud.nanolika.ru").rstrip("/")

    # куда возвращать человека после разрешения/отказа: только свои домены
    allowed_next = ("https://nanolika.ru", "https://cloud.nanolika.ru")

    def pick_next(raw: str) -> str:
        raw = (raw or "").strip()
        for host in allowed_next:
            if raw == host or raw.startswith(host + "/"):
                return raw
        return f"{base}/app/"

    def cookie_domain() -> str:
        host = urllib.parse.urlparse(base).hostname or ""
        # общая кука для витрины и консоли: .nanolika.ru
        parts = host.split(".")
        return "." + ".".join(parts[-2:]) if len(parts) >= 2 else host

    def _back(target: str, reason: str):
        sep = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{sep}auth_error={urllib.parse.quote(reason)}", status_code=302)

    def redirect_uri(provider: str) -> str:
        # адрес возврата берём из PUBLIC_BASE, а не из запроса: за прокси Caddy
        # request.base_url может прийти http-шным, и провайдер откажет
        return f"{base}/v1/auth/oauth/{provider}/callback"

    @app.get("/v1/auth/oauth/providers")
    async def oauth_providers() -> dict:
        return {"providers": enabled_providers()}

    @app.get("/v1/auth/oauth/{provider}/start")
    async def oauth_start(provider: str, next: str = ""):
        if provider not in PROVIDERS:
            return JSONResponse({"error": {"code": "no_provider"}}, status_code=404)
        state = provider_state(provider)
        if not state["enabled"]:
            return JSONResponse({"error": {"code": "provider_off", "provider": provider}}, status_code=503)
        nonce = secrets.token_urlsafe(24)
        target = authorize_url(provider, redirect_uri(provider), nonce)
        response = RedirectResponse(target, status_code=302)
        nxt = pick_next(next or "")
        response.set_cookie("nm_oauth", f"{provider}:{nonce}:{nxt}", max_age=STATE_TTL_S,
                            httponly=True, secure=True, samesite="lax", path="/")
        return response

    @app.get("/v1/auth/oauth/{provider}/callback")
    async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
        cookie = request.cookies.get("nm_oauth", "")
        parts = cookie.split(":", 2)
        want = parts[1] if len(parts) >= 2 else ""
        nxt = parts[2] if len(parts) == 3 else f"{base}/app/"
        if error or not code or not want or not hmac.compare_digest(want, state):
            return _back(nxt, "denied" if error else "state")
        try:
            profile = fetch_profile(provider, code, redirect_uri(provider))
        except Exception as e:                          # noqa: BLE001
            log.warning("oauth %s: %s", provider, e)
            return _back(nxt, type(e).__name__)
        email = (profile.get("email") or "").strip().lower()
        if not email:
            return _back(nxt, "no_email")
        try:
            ident = parse_identifier(email)
            api_key, caller, created = cloud.sign_in(ident, "web", f"oauth.{provider}", reached=True)
        except Exception as e:                          # noqa: BLE001
            log.warning("oauth %s sign_in: %s", provider, e)
            code_name = getattr(e, "code", type(e).__name__)
            return _back(nxt, str(code_name))
        log.info("oauth %s: account %s (%s)", provider, caller.account_id[:8],
                 "created" if created else "existing")
        # ключ всегда уходит консоли: без него она не знает о входе; адрес возврата
        # (витрина, профиль, любая страница) консоль откроет сама после сохранения
        target = f"{base}/app/?next={urllib.parse.quote(nxt, safe='')}#sso={api_key}"
        response = RedirectResponse(target, status_code=302)
        response.delete_cookie("nm_oauth", path="/")
        # общая метка входа для витрины: сам ключ не отдаём, только факт входа
        response.set_cookie("nm_signed", caller.account_id[:8], max_age=30 * 86400,
                            domain=cookie_domain(), secure=True, samesite="lax", path="/")
        return response

    @app.get("/v1/auth/logout")
    async def oauth_logout(next: str = ""):
        """Выход с витрины: снимает общую метку входа и уводит назад на сайт."""
        target = pick_next(next or "https://nanolika.ru/")
        response = RedirectResponse(target, status_code=302)
        response.delete_cookie("nm_signed", domain=cookie_domain(), path="/")
        response.delete_cookie("nm_oauth", path="/")
        return response

    @app.post("/v1/auth/oauth/telegram")
    async def oauth_telegram(payload: dict = Body(...)) -> dict:
        if not provider_state("telegram")["enabled"]:
            return JSONResponse({"error": {"code": "provider_off", "provider": "telegram"}}, status_code=503)
        profile = check_telegram(payload)
        if profile is None:
            return JSONResponse({"error": {"code": "bad_signature", "type": "nanomuse_cloud"}}, status_code=401)
        ident = telegram_identifier(profile["subject"])
        api_key, caller, created = cloud.sign_in(ident, "web", "oauth.telegram", reached=True)
        return {"api_key": api_key, "created": created,
                "account": {"id": caller.account_id, "hint": profile["name"]}}

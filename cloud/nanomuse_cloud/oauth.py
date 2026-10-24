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
    from fastapi import Body, Request
    from fastapi.responses import JSONResponse, RedirectResponse

    from .identifiers import parse as parse_identifier, telegram as telegram_identifier

    base = (getattr(settings, "public_base", "") or "https://cloud.nanolika.ru").rstrip("/")

    def redirect_uri(provider: str, request: Request) -> str:
        return f"{str(request.base_url).rstrip('/')}/v1/auth/oauth/{provider}/callback"

    @app.get("/v1/auth/oauth/providers")
    async def oauth_providers() -> dict:
        return {"providers": enabled_providers()}

    @app.get("/v1/auth/oauth/{provider}/start")
    async def oauth_start(provider: str, request: Request):
        if provider not in PROVIDERS:
            return JSONResponse({"error": {"code": "no_provider"}}, status_code=404)
        state = provider_state(provider)
        if not state["enabled"]:
            return JSONResponse({"error": {"code": "provider_off", "provider": provider}}, status_code=503)
        nonce = secrets.token_urlsafe(24)
        target = authorize_url(provider, redirect_uri(provider, request), nonce)
        response = RedirectResponse(target, status_code=302)
        response.set_cookie("nm_oauth", f"{provider}:{nonce}", max_age=STATE_TTL_S,
                            httponly=True, secure=True, samesite="lax", path="/")
        return response

    @app.get("/v1/auth/oauth/{provider}/callback")
    async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
        cookie = request.cookies.get("nm_oauth", "")
        want = cookie.split(":", 1)[1] if ":" in cookie else ""
        if error or not code or not want or not hmac.compare_digest(want, state):
            return RedirectResponse(f"{base}/app/?sso_error={urllib.parse.quote(error or 'state')}#signin",
                                    status_code=302)
        try:
            profile = fetch_profile(provider, code, redirect_uri(provider, request))
        except Exception as e:                          # noqa: BLE001
            log.warning("oauth %s: %s", provider, e)
            return RedirectResponse(f"{base}/app/?sso_error={urllib.parse.quote(type(e).__name__)}#signin",
                                    status_code=302)
        email = (profile.get("email") or "").strip().lower()
        if not email:
            return RedirectResponse(f"{base}/app/?sso_error=no_email#signin", status_code=302)
        try:
            ident = parse_identifier(email)
            api_key, caller, created = cloud.sign_in(ident, "web", f"oauth.{provider}", reached=True)
        except Exception as e:                          # noqa: BLE001
            log.warning("oauth %s sign_in: %s", provider, e)
            code_name = getattr(e, "code", type(e).__name__)
            return RedirectResponse(f"{base}/app/?sso_error={urllib.parse.quote(str(code_name))}#signin",
                                    status_code=302)
        log.info("oauth %s: account %s (%s)", provider, caller.account_id[:8],
                 "created" if created else "existing")
        response = RedirectResponse(f"{base}/app/#sso={api_key}", status_code=302)
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

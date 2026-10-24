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

import asyncio
import base64
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

from .browser_auth import browser_marker

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
        scope="login:email",
        id_env="CLOUD_OAUTH_YANDEX_ID",
        secret_env="CLOUD_OAUTH_YANDEX_SECRET",
    ),
    "google": dict(
        title="Google",
        authorize="https://accounts.google.com/o/oauth2/v2/auth",
        token="https://oauth2.googleapis.com/token",
        profile="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email",
        id_env="CLOUD_OAUTH_GOOGLE_ID",
        secret_env="CLOUD_OAUTH_GOOGLE_SECRET",
    ),
    "github": dict(
        title="GitHub",
        authorize="https://github.com/login/oauth/authorize",
        token="https://github.com/login/oauth/access_token",
        profile="https://api.github.com/user",
        scope="read:user user:email",
        id_env="CLOUD_OAUTH_GITHUB_ID",
        secret_env="CLOUD_OAUTH_GITHUB_SECRET",
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
        return dict(
            name=name,
            title=conf["title"],
            kind="widget",
            enabled=bool(_env(conf["token_env"]) and _env(conf["bot_env"])),
            bot=_env(conf["bot_env"]),
        )
    enabled = bool(_env(conf["id_env"]) and _env(conf["secret_env"]))
    return dict(name=name, title=conf["title"], kind="redirect", enabled=enabled)


def enabled_providers() -> list[dict]:
    return [s for s in (provider_state(n) for n in ("yandex", "google")) if s["enabled"]]


# ── OAuth-редирект ──────────────────────────────────────────────────────
def authorize_url(provider: str, redirect_uri: str, state: str, verifier: str = "") -> str:
    conf = PROVIDERS[provider]
    params = {
        "client_id": _env(conf["id_env"]),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": conf["scope"],
        "state": state,
    }
    if verifier:
        params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params["code_challenge_method"] = "S256"
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return conf["authorize"] + "?" + urllib.parse.urlencode(params)


def _post(url: str, data: dict, headers: dict | None = None) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": UA,
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _get(url: str, token: str, scheme: str = "Bearer") -> str:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"{scheme} {token}",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def fetch_profile(provider: str, code: str, redirect_uri: str, verifier: str = "") -> dict:
    """Меняет код на профиль. Возвращает {subject, email, name}."""
    conf = PROVIDERS[provider]
    token_body = _post(
        conf["token"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": _env(conf["id_env"]),
            "client_secret": _env(conf["secret_env"]),
            "redirect_uri": redirect_uri,
            **({"code_verifier": verifier} if verifier else {}),
        },
        headers={"Accept": "application/json"},
    )
    try:
        token = json.loads(token_body).get("access_token", "")
    except json.JSONDecodeError:  # Яндекс умеет form-urlencoded
        token = urllib.parse.parse_qs(token_body).get("access_token", [""])[0]
    if not token:
        raise RuntimeError(f"{provider}: токен не выдан")

    if provider == "github":
        raw = json.loads(_get("https://api.github.com/user", token))
        email = raw.get("email") or ""
        if not email:  # приватная почта — спросим отдельно
            mails = json.loads(_get("https://api.github.com/user/emails", token))
            primary = [m for m in mails if m.get("primary") and m.get("verified")]
            email = primary[0]["email"] if primary else ""
        return dict(subject=str(raw.get("id", "")), email=email, name=raw.get("login") or "")

    raw = json.loads(_get(conf["profile"], token, "OAuth" if provider == "yandex" else "Bearer"))
    if provider == "yandex":
        if not raw.get("id") or (raw.get("client_id") and raw["client_id"] != _env(conf["id_env"])):
            raise ValueError("Invalid Yandex identity")
        email = raw.get("default_email") or ""
        if not email:
            emails = raw.get("emails") or []
            email = emails[0] if emails else ""
        return dict(subject=str(raw.get("id", "")), email=email, name=raw.get("login") or raw.get("real_name") or "")
    if not raw.get("sub") or raw.get("email_verified") is not True:
        raise ValueError("A verified Google email is required")
    email = raw.get("email") or ""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain not in {"gmail.com", "googlemail.com"} and not (raw.get("hd") and str(raw["hd"]).lower() == domain):
        raise ValueError("Confirm this email using the email code instead")
    return dict(subject=str(raw.get("sub", "")), email=email, name=raw.get("name") or raw.get("given_name") or "")


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
    return dict(
        subject=str(data.get("id", "")),
        name=" ".join(x for x in (data.get("first_name"), data.get("last_name")) if x) or data.get("username") or "",
    )


def safe_next(raw: str, base: str) -> str:
    try:
        parsed = urllib.parse.urlsplit((raw or "").strip())
        base_host = urllib.parse.urlsplit(base).netloc
        if parsed.scheme == "https" and parsed.netloc in {"nanolika.ru", base_host} and not parsed.username and not parsed.password:
            # Never carry an OAuth fragment or a raw token back to the marketing site.
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except ValueError:
        pass
    return base + "/web/"


def state_cookie(data: dict, key: str | bytes) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    signature = hmac.new(key if isinstance(key, bytes) else key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def read_state(cookie: str, key: str | bytes, provider: str, nonce: str) -> dict | None:
    try:
        payload, signature = cookie.rsplit(".", 1)
        expected = hmac.new(key if isinstance(key, bytes) else key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data.get("provider") != provider or not nonce or not hmac.compare_digest(str(data.get("nonce", "")), nonce):
            return None
        expires = int(data.get("expires", 0))
        if expires <= time.time() or expires > time.time() + STATE_TTL_S + 5:
            return None
        return data
    except (ValueError, TypeError, KeyError):
        return None


# ── ручки ───────────────────────────────────────────────────────────────
def install_oauth(app, cloud, settings) -> None:
    """Три ручки: список провайдеров, старт и возврат. Плюс вход по виджету Telegram."""
    from fastapi.responses import JSONResponse, RedirectResponse

    from .identifiers import parse as parse_identifier

    base = (getattr(settings, "public_base", "") or "https://cloud.nanolika.ru").rstrip("/")

    def pick_next(raw: str) -> str:
        return safe_next(raw, base)

    def _back(target: str, reason: str):
        target = pick_next(target)
        # Auth failures return to the form, where they are explained, not lost on the site.
        response = RedirectResponse(
            base + "/app/?signin=1&next=" + urllib.parse.quote(target, safe="") + "&auth_error=" + urllib.parse.quote(reason),
            status_code=302,
        )
        response.delete_cookie("nm_oauth", path="/")
        return response

    def redirect_uri(provider: str) -> str:
        # адрес возврата берём из PUBLIC_BASE, а не из запроса: за прокси Caddy
        # request.base_url может прийти http-шным, и провайдер откажет
        return f"{base}/v1/auth/oauth/{provider}/callback"

    @app.get("/v1/auth/oauth/providers")
    async def oauth_providers() -> dict:
        return {"providers": enabled_providers()}

    @app.get("/v1/auth/oauth/{provider}/start")
    async def oauth_start(provider: str, next: str = ""):
        if provider not in {"yandex", "google"}:
            return JSONResponse({"error": {"code": "no_provider"}}, status_code=404)
        state = provider_state(provider)
        if not state["enabled"]:
            return JSONResponse({"error": {"code": "provider_off", "provider": provider}}, status_code=503)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48) if provider == "google" else ""
        target = authorize_url(provider, redirect_uri(provider), nonce, verifier)
        response = RedirectResponse(target, status_code=302)
        nxt = pick_next(next or "")
        response.set_cookie(
            "nm_oauth",
            state_cookie(
                {"provider": provider, "nonce": nonce, "next": nxt, "expires": int(time.time()) + STATE_TTL_S, "verifier": verifier},
                settings.hmac_key,
            ),
            max_age=STATE_TTL_S,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/v1/auth/oauth/{provider}/callback")
    async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
        if provider not in {"yandex", "google"} or not provider_state(provider)["enabled"]:
            return JSONResponse({"error": {"code": "provider_off"}}, status_code=404)
        data = read_state(request.cookies.get("nm_oauth", ""), settings.hmac_key, provider, state)
        nxt = pick_next(data.get("next", "") if data else "")
        if error or not code or data is None:
            return _back(nxt, "denied" if error else "state")
        try:
            profile = await asyncio.to_thread(fetch_profile, provider, code, redirect_uri(provider), data.get("verifier", ""))
        except Exception as e:  # noqa: BLE001
            log.warning("oauth %s profile failed: %s", provider, type(e).__name__)
            return _back(nxt, type(e).__name__)
        email = (profile.get("email") or "").strip().lower()
        if not email:
            return _back(nxt, "no_email")
        try:
            ident = parse_identifier(email)
            api_key, caller, created = await asyncio.to_thread(cloud.sign_in, ident, "nanoLika Web", f"oauth.{provider}")
        except Exception as e:  # noqa: BLE001
            log.warning("oauth %s sign_in: %s", provider, type(e).__name__)
            code_name = getattr(e, "code", type(e).__name__)
            return _back(nxt, str(code_name))
        log.info("oauth %s: account %s (%s)", provider, caller.account_id[:8], "created" if created else "existing")
        # ключ всегда уходит консоли: без него она не знает о входе; адрес возврата
        # (витрина, профиль, любая страница) консоль откроет сама после сохранения
        target = f"{base}/app/?next={urllib.parse.quote(nxt, safe='')}#sso={api_key}"
        response = RedirectResponse(target, status_code=302)
        response.delete_cookie("nm_oauth", path="/")
        browser_marker(response, settings)
        if created:
            welcome = getattr(cloud.sender, "send_first_login", None)
            if callable(welcome):
                try:
                    await asyncio.to_thread(welcome, ident)
                except Exception as exc:
                    log.warning("oauth welcome failed: %s", type(exc).__name__)
        return response

    @app.get("/v1/auth/logout")
    async def oauth_logout(next: str = ""):
        """Выход с витрины: снимает общую метку входа и уводит назад на сайт."""
        target = pick_next(next or "https://nanolika.ru/")
        response = RedirectResponse(target, status_code=302)
        browser_marker(response, settings, signed_in=False)
        response.delete_cookie("nm_oauth", path="/")
        return response

    @app.post("/v1/auth/oauth/telegram")
    async def oauth_telegram() -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "email_required", "message": "Use email, Yandex or Google to sign in. Link messengers from your account."}},
            status_code=410,
        )

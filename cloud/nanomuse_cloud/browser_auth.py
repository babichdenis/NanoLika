"""Public presence marker and host-only opaque HttpOnly cookie. APIs require Bearer."""

from urllib.parse import urlsplit

COOKIE = "nm_signed"
SESSION_COOKIE = "__Host-nanolika_session"


def browser_marker(response, settings, *, signed_in: bool = True, session_token: str = "", max_age: int = 30 * 86400):
    host = urlsplit(settings.public_base).hostname or ""
    domain = ".nanolika.ru" if host == "nanolika.ru" or host.endswith(".nanolika.ru") else None
    if signed_in:
        response.set_cookie(COOKIE, "1", max_age=30 * 86400, secure=True, samesite="lax", path="/", domain=domain)
        if session_token:
            response.set_cookie(SESSION_COOKIE, session_token, max_age=max_age, secure=True, httponly=True, samesite="lax", path="/")
    else:
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        response.delete_cookie(COOKIE, domain=domain, path="/", secure=True, samesite="lax")
        # Older website builds also set a host-only presence marker.
        response.delete_cookie(COOKIE, path="/", secure=True, samesite="lax")
    return response


def browser_sign_in(response, cloud, caller, *, previous_token: str = "", legacy: bool = False):
    from .browser_session_store import browser_store

    store = browser_store(cloud)
    token, max_age = store.issue(caller, legacy=legacy)
    store.revoke(previous_token)
    return browser_marker(response, cloud.s, session_token=token, max_age=max_age)

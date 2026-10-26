"""Public presence marker and host-only HttpOnly web-session cookie. APIs still require Bearer."""

from urllib.parse import urlsplit

COOKIE = "nm_signed"
SESSION_COOKIE = "__Host-nanolika_session"


def browser_marker(response, settings, *, signed_in: bool = True, api_key: str = ""):
    host = urlsplit(settings.public_base).hostname or ""
    domain = ".nanolika.ru" if host == "nanolika.ru" or host.endswith(".nanolika.ru") else None
    if signed_in:
        response.set_cookie(COOKIE, "1", max_age=30 * 86400, secure=True, samesite="lax", path="/", domain=domain)
        if api_key:
            response.set_cookie(SESSION_COOKIE, api_key, max_age=30 * 86400, secure=True, httponly=True, samesite="lax", path="/")
    else:
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        response.delete_cookie(COOKIE, domain=domain, path="/", secure=True, samesite="lax")
    return response

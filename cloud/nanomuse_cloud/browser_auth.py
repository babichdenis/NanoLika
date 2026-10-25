"""Browser-only presence marker, never an authentication credential."""

from urllib.parse import urlsplit

COOKIE = "nm_signed"


def browser_marker(response, settings, *, signed_in: bool = True):
    host = urlsplit(settings.public_base).hostname or ""
    domain = ".nanolika.ru" if host == "nanolika.ru" or host.endswith(".nanolika.ru") else None
    if signed_in:
        response.set_cookie(COOKIE, "1", max_age=30 * 86400, secure=True, samesite="lax", path="/", domain=domain)
    else:
        response.delete_cookie(COOKIE, domain=domain, path="/", secure=True, samesite="lax")
    return response

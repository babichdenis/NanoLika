"""Web search for the relay: free backends first, so that search works out of the box.

The desktop's own search provider is DeepSeek's, and it needs a DeepSeek account or an
API key that a person on NanoLika Cloud does not have. A person should not have to buy a
second account to find a page, so the relay searches for them, with backends that charge
nothing per query:

* **searxng**, when the operator runs one (`CLOUD_SEARXNG_URL`): a metasearch engine of
  their own, no key and no quota. Preferred, because the operator controls it.
* **duckduckgo**, the HTML endpoint, no key. Its markup is what `parse_duckduckgo` reads;
  DuckDuckGo asks bots to be gentle, so this is one request per search and no paging.
* **wikipedia**, the own-language API: for a question with a name in it, this is the answer
  most often wanted, and it is free.
* **github**, repositories and code (`GITHUB_TOKEN` when the operator has one): what a
  person looking for a workaround actually wants, and what `kind=code` selects.

Nothing here is billed to the account: a search spends no tokens and no grant. The relay
does not store the query; only the operator's own access log sees it.
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

# One search may look at most this many backends; the answer keeps the first `limit` sources.
DEFAULT_LIMIT = 8
MAX_LIMIT = 20


@dataclass(frozen=True)
class Source:
    """One citeable hit: a URL always, the words around it when the backend has them."""

    url: str
    title: str = ""
    snippet: str = ""


def _text(fragment: str) -> str:
    """A snippet as plain text: tags dropped, entities decoded, whitespace collapsed."""
    without_tags = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(without_tags)).strip()


def _clean_url(href: str) -> str:
    """DuckDuckGo wraps the target: `/l/?uddg=<quoted>` is the real address."""
    href = htmllib.unescape(href)
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


def parse_duckduckgo(page: str, limit: int = DEFAULT_LIMIT) -> list[Source]:
    """The HTML endpoint's results: a titled link, then its snippet in the next block."""
    titles = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        re.DOTALL,
    )
    snippets = re.findall(
        r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        page,
        re.DOTALL,
    )
    sources: list[Source] = []
    for index, (href, title) in enumerate(titles):
        url = _clean_url(href)
        if not url.startswith("http"):
            continue
        snippet = _text(snippets[index]) if index < len(snippets) else ""
        sources.append(Source(url=url, title=_text(title), snippet=snippet))
        if len(sources) >= limit:
            break
    return sources


def parse_searxng(payload: dict, limit: int = DEFAULT_LIMIT) -> list[Source]:
    """SearXNG's JSON: `results[]` with `url`, `title`, `content`."""
    sources: list[Source] = []
    for row in payload.get("results", [])[:limit]:
        url = str(row.get("url") or "")
        if not url.startswith("http"):
            continue
        sources.append(Source(url=url, title=str(row.get("title") or ""), snippet=str(row.get("content") or "")))
    return sources


def parse_wikipedia(payload: dict, lang: str = "ru", limit: int = DEFAULT_LIMIT) -> list[Source]:
    """The action API's `query.search[]`: page titles, with a plain-text extract."""
    sources: list[Source] = []
    for row in payload.get("query", {}).get("search", [])[:limit]:
        title = str(row.get("title") or "")
        if not title:
            continue
        sources.append(
            Source(
                url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                title=title,
                snippet=_text(str(row.get("snippet") or "")),
            )
        )
    return sources


def parse_github(payload: dict, limit: int = DEFAULT_LIMIT) -> list[Source]:
    """Both search endpoints answer `items[]`; a repository and a code hit differ in shape."""
    sources: list[Source] = []
    for row in payload.get("items", [])[:limit]:
        url = str(row.get("html_url") or "")
        if not url.startswith("http"):
            continue
        if "repository" in row:
            repo = row["repository"]
            sources.append(
                Source(
                    url=url,
                    title=str(repo.get("full_name") or row.get("name") or ""),
                    snippet=str(row.get("text_matches") and row["text_matches"][0].get("fragment") or row.get("path") or ""),
                )
            )
        else:
            sources.append(
                Source(
                    url=url,
                    title=str(row.get("full_name") or ""),
                    snippet=str(row.get("description") or ""),
                )
            )
    return sources


class SearchError(RuntimeError):
    """Every backend failed; the message says which and why, one line."""

    def __init__(self, tried: list[str]) -> None:
        super().__init__(f"no search backend answered ({', '.join(tried) or 'none configured'})")
        self.tried = tried


async def search(
    http: httpx.AsyncClient,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    kind: str = "web",
    searxng_url: str = "",
    github_token: str = "",
    wikipedia_lang: str = "ru",
) -> dict:
    """Search with the free backends in order; the first that answers wins.

    Returns `{"sources": [{url, title, snippet}], "backend": "searxng", "query": q}`.
    Raises `SearchError` when nothing answered, so the client can say so plainly instead of
    showing an empty page as an answer.
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    query = query.strip()[:400]
    if not query:
        return {"sources": [], "backend": "", "query": ""}
    tried: list[str] = []

    async def attempt(name: str, call) -> list[Source] | None:
        try:
            sources = await call()
        except (httpx.HTTPError, ValueError, KeyError):
            tried.append(name)
            return None
        if not sources:
            tried.append(name)
            return None
        return sources[:limit]

    if kind == "code" and github_token:
        sources = await attempt(
            "github",
            lambda: _github(http, query, limit, github_token, code=True),
        )
        if sources:
            return {"sources": _json_sources(sources), "backend": "github", "query": query}

    if searxng_url:
        sources = await attempt("searxng", lambda: _searxng(http, query, limit, searxng_url))
        if sources:
            return {"sources": _json_sources(sources), "backend": "searxng", "query": query}

    sources = await attempt("duckduckgo", lambda: _duckduckgo(http, query, limit))
    if sources:
        return {"sources": _json_sources(sources), "backend": "duckduckgo", "query": query}

    sources = await attempt("wikipedia", lambda: _wikipedia(http, query, limit, wikipedia_lang))
    if sources:
        return {"sources": _json_sources(sources), "backend": "wikipedia", "query": query}

    if github_token and kind != "code":
        sources = await attempt("github", lambda: _github(http, query, limit, github_token, code=False))
        if sources:
            return {"sources": _json_sources(sources), "backend": "github", "query": query}

    raise SearchError(tried)


def _json_sources(sources: list[Source]) -> list[dict]:
    return [{"url": s.url, "title": s.title, "snippet": s.snippet} for s in sources]


async def _duckduckgo(http: httpx.AsyncClient, query: str, limit: int) -> list[Source]:
    response = await http.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; NanoLika Cloud search)"},
    )
    response.raise_for_status()
    return parse_duckduckgo(response.text, limit)


async def _searxng(http: httpx.AsyncClient, query: str, limit: int, base: str) -> list[Source]:
    response = await http.get(
        f"{base.rstrip('/')}/search",
        params={"q": query, "format": "json", "language": "auto"},
    )
    response.raise_for_status()
    return parse_searxng(response.json(), limit)


async def _wikipedia(http: httpx.AsyncClient, query: str, limit: int, lang: str) -> list[Source]:
    response = await http.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": limit,
        },
    )
    response.raise_for_status()
    return parse_wikipedia(response.json(), lang, limit)


async def _github(http: httpx.AsyncClient, query: str, limit: int, token: str, *, code: bool) -> list[Source]:
    endpoint = "code" if code else "repositories"
    response = await http.get(
        f"https://api.github.com/search/{endpoint}",
        params={"q": query, "per_page": limit},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.text-match+json" if code else "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return parse_github(response.json(), limit)

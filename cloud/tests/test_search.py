"""The free search backends: the parsers, which is where a backend's shape is decided.

No network: every test feeds a recorded fragment of the real answer, so a change in the
relay's own plumbing cannot hide a broken parse.
"""

from __future__ import annotations

from nanomuse_cloud import search


def test_duckduckgo_takes_the_title_the_link_and_the_snippet() -> None:
    page = """
    <div class="result results_links">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&amp;rut=abc">Example <b>title</b></a>
      <a class="result__snippet">A snippet with &amp; an entity</a>
    </div>
    <div class="result results_links">
      <a class="result__a" href="https://second.example/b">Second</a>
    </div>
    """
    sources = search.parse_duckduckgo(page)
    assert [s.url for s in sources] == ["https://example.com/a?x=1", "https://second.example/b"]
    assert sources[0].title == "Example title"
    assert sources[0].snippet == "A snippet with & an entity"
    assert sources[1].snippet == ""


def test_searxng_reads_its_results_list() -> None:
    payload = {
        "results": [
            {"url": "https://a.example/1", "title": "One", "content": "first"},
            {"url": "javascript:void(0)", "title": "junk"},
            {"url": "https://b.example/2", "title": "Two"},
        ]
    }
    sources = search.parse_searxng(payload)
    assert [s.url for s in sources] == ["https://a.example/1", "https://b.example/2"]
    assert sources[0].snippet == "first"


def test_wikipedia_builds_the_page_url_and_drops_markup() -> None:
    payload = {"query": {"search": [{"title": "Кошка", "snippet": 'A <span class="searchmatch">cat</span>'}]}}
    sources = search.parse_wikipedia(payload, "ru")
    assert sources[0].url == "https://ru.wikipedia.org/wiki/%D0%9A%D0%BE%D1%88%D0%BA%D0%B0"
    assert sources[0].snippet == "A cat"


def test_github_reads_both_a_repository_and_a_code_hit() -> None:
    payload = {
        "items": [
            {"full_name": "nano-muse/nanoMuse", "html_url": "https://github.com/nano-muse/nanoMuse", "description": "an agent"},
            {
                "html_url": "https://github.com/o/r/blob/main/x.py",
                "path": "x.py",
                "repository": {"full_name": "o/r"},
            },
        ]
    }
    sources = search.parse_github(payload)
    assert sources[0].title == "nano-muse/nanoMuse"
    assert sources[0].snippet == "an agent"
    assert sources[1].title == "o/r"
    assert sources[1].snippet == "x.py"


def test_an_empty_query_asks_nobody() -> None:
    import asyncio

    import httpx

    async def run() -> dict:
        async with httpx.AsyncClient() as http:
            return await search.search(http, "   ")

    assert asyncio.run(run()) == {"sources": [], "backend": "", "query": ""}

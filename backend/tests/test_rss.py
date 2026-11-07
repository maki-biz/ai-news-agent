"""合成フィードによる日時・形式・障害分離の検証。"""

from datetime import date

import httpx
import pytest

from backend.config import Settings
from backend.schemas import SearchRequest
from backend.tools.hatena import HatenaNewsSource
from backend.tools.http import ToolError, request_response
from backend.tools.rss import RssNewsSource, parse_feed
from backend.tools.zenn import ZennNewsSource

REQUEST = SearchRequest(
    query="AI", start_date=date(2026, 11, 1), end_date=date(2026, 11, 7)
)
RSS = b"""<rss version="2.0"><channel><title>Example</title><link>https://example.com</link><description>Test</description>
<item><title>AI news</title><link>/news?utm_source=feed</link><pubDate>Fri, 06 Nov 2026 23:30:00 GMT</pubDate><description>AI details</description></item>
<item><title>Unrelated</title><link>/other</link></item>
<item><title>AI invalid</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


def test_rss_dates_and_relative_url() -> None:
    result = parse_feed(
        RSS,
        "https://example.com/feed",
        "rss",
        REQUEST.model_copy(update={"timezone": "Asia/Tokyo"}),
    )
    assert len(result.articles) == 1
    article = result.articles[0]
    assert article.published_at == date(2026, 11, 7)
    assert str(article.url) == "https://example.com/news?utm_source=feed"
    assert article.provenance[0].source_id == "rss"
    assert "rss: invalid_feed_entry" in result.warnings


@pytest.mark.parametrize(
    "field,expected",
    [
        ("<published>2026-11-06</published>", date(2026, 11, 6)),
        ("<published>2026-11-06T12:00:00</published>", None),
        ("<updated>2026-11-06T12:00:00Z</updated>", None),
        ("<published>not a date</published>", None),
    ],
)
def test_atom_does_not_invent_publication(field: str, expected: date | None) -> None:
    feed = f'<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title><entry><title>AI</title><link href="https://example.com/a"/>{field}</entry></feed>'
    result = parse_feed(feed.encode(), "https://example.com/feed", "rss", REQUEST)
    assert result.articles[0].published_at == expected


def test_rdf_and_hatena_dates() -> None:
    feed = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <channel rdf:about="https://example.com/feed"><title>Test</title><link>https://example.com</link><description>Test</description></channel>
    <item rdf:about="https://example.com/a"><title>AI</title><link>https://example.com/a</link><dc:date>2026-11-06T00:00:00Z</dc:date></item></rdf:RDF>"""
    result = parse_feed(
        feed, "https://example.com/feed", "hatena", REQUEST, publication_dates=False
    )
    assert len(result.articles) == 1
    assert result.articles[0].published_at is None


@pytest.mark.asyncio
async def test_feed_failures_are_independent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(500)
            if request.url.path == "/bad"
            else httpx.Response(200, content=RSS)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(
            _env_file=None,
            rss_feeds=["https://example.com/bad", "https://example.com/good"],
        )
        result = await RssNewsSource(client, settings).search(REQUEST)
    assert len(result.articles) == 1
    assert [s.status for s in result.source_statuses] == ["failed", "partial"]


@pytest.mark.asyncio
async def test_hatena_and_zenn_adapters() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=RSS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(_env_file=None, zenn_topics=["ai"])
        hatena = await HatenaNewsSource(client, settings).search(REQUEST)
        zenn = await ZennNewsSource(client, settings).search(REQUEST)
    assert hatena.articles[0].published_at is None
    assert zenn.articles[0].published_at == date(2026, 11, 6)
    assert "https://zenn.dev/topics/ai/feed" in seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["http://127.0.0.1/feed", "https://other.example/feed", "http://example.com/feed"],
)
async def test_redirect_restrictions(target: str) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": target})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError):
            await request_response(
                client, "GET", "https://example.com/feed", feed_redirects=True
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_size_limit_and_gzip() -> None:
    import gzip

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=gzip.compress(RSS), headers={"content-encoding": "gzip"}
            )
        )
    ) as client:
        response = await request_response(client, "GET", "https://example.com")
        assert response.content == RSS
        with pytest.raises(ToolError, match="response_too_large"):
            await request_response(client, "GET", "https://example.com", max_bytes=10)


def test_invalid_feed() -> None:
    with pytest.raises(ToolError, match="invalid_feed"):
        parse_feed(b"not xml", "https://example.com", "rss", REQUEST)


@pytest.mark.asyncio
async def test_free_retry_is_bounded_and_honors_long_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits, calls = [], []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("backend.tools.http.asyncio.sleep", sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="rate_limited"):
            await request_response(client, "GET", "https://example.com", retries=10)
    assert len(calls) == 2
    assert waits == [1]
    calls.clear()

    def long_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "30"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(long_handler)) as client:
        with pytest.raises(ToolError) as exc:
            await request_response(client, "GET", "https://example.com", retries=1)
    assert len(calls) == 1
    assert exc.value.retry_after == "30"


@pytest.mark.asyncio
async def test_rss_timeout_keeps_prior_feed() -> None:
    import asyncio

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/slow":
            await asyncio.sleep(1)
        return httpx.Response(200, content=RSS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(
            _env_file=None,
            source_deadline_seconds=0.05,
            rss_feeds=["https://example.com/good", "https://example.com/slow"],
        )
        result = await RssNewsSource(client, settings).search(REQUEST)
    assert len(result.articles) == 1
    assert result.source_statuses[1].error == "deadline_exceeded"

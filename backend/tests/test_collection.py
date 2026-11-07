"""複数ソース統合・コスト制御・URL正規化の検証。"""

import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest
from pydantic import HttpUrl, SecretStr

from backend.config import Settings
from backend.schemas import Article, Provenance, SearchRequest, SourceResult
from backend.services.search import CombinedSource
from backend.services.search import search_news as organize
from backend.services.urls import normalize_url
from backend.tools.http import ToolError
from backend.tools.openai_search import OpenAINewsSource
from backend.tools.search import search_news

REQ = SearchRequest(
    query="AI", start_date=date(2026, 11, 1), end_date=date(2026, 11, 7)
)


class FakeSource:
    async def search(self, request: SearchRequest) -> SourceResult:
        return SourceResult(articles=[], source_urls=[])


class BrokenSource:
    async def search(self, request: SearchRequest) -> SourceResult:
        raise ToolError("rate_limited", "30")


@pytest.mark.asyncio
async def test_partial_and_empty_success() -> None:
    result = await organize(
        REQ, CombinedSource({"bad": BrokenSource(), "empty": FakeSource()})
    )
    assert result.articles == []
    assert [s.status for s in result.source_statuses] == ["failed", "ok"]
    assert result.source_statuses[0].retry_after == "30"
    with pytest.raises(ToolError, match="all_sources_failed"):
        await organize(REQ, CombinedSource({"bad": BrokenSource()}))


@pytest.mark.asyncio
async def test_source_deadline() -> None:
    class SlowSource:
        async def search(self, request: SearchRequest) -> SourceResult:
            await asyncio.sleep(1)

    result = await organize(
        REQ,
        CombinedSource({"slow": SlowSource(), "empty": FakeSource()}, deadline=0.001),
    )
    assert result.source_statuses[0].error == "deadline_exceeded"


@pytest.mark.asyncio
async def test_default_sources_never_contact_openai() -> None:
    contacted = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(request.url.host)
        assert request.url.host != "api.openai.com"
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="all_sources_failed"):
            await search_news(
                "AI",
                REQ.start_date,
                REQ.end_date,
                client=client,
                settings=Settings(
                    _env_file=None,
                    openai_api_key=SecretStr("test"),
                    paid_search_enabled=True,
                ),
            )
    assert set(contacted) == {
        "huggingface.co",
        "www.jpcert.or.jp",
        "b.hatena.ne.jp",
        "zenn.dev",
        "qiita.com",
        "services.nvd.nist.gov",
    }


@pytest.mark.asyncio
async def test_paid_search_disabled_at_both_entrypoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("disabled paid search must not send any request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="paid_search_disabled"):
            await search_news(
                "AI",
                REQ.start_date,
                REQ.end_date,
                client=client,
                sources=["openai"],
                settings=Settings(_env_file=None),
            )
        with pytest.raises(ToolError, match="paid_search_disabled"):
            await OpenAINewsSource(client, SecretStr("test"), "test").search(REQ)


@pytest.mark.asyncio
async def test_dedup_preserves_provenance_and_known_date() -> None:
    def article(name: str, url: str, published: date | None) -> Article:
        return Article(
            title=name,
            url=url,
            published_at=published,
            provenance=[
                Provenance(
                    source_id=name,
                    source_url="https://example.com/feed",
                    retrieved_at=datetime.now(UTC),
                )
            ],
        )

    class Source:
        async def search(self, request: SearchRequest) -> SourceResult:
            return SourceResult(
                articles=[
                    article("hatena", "https://example.com/a?utm_source=x", None),
                    article("official", "https://example.com/a", date(2026, 11, 6)),
                    article("old", "https://example.com/old", date(2020, 1, 1)),
                ],
                source_urls=[],
            )

    result = await organize(REQ, Source())
    assert len(result.articles) == 1
    assert result.articles[0].title == "official"
    assert [p.source_id for p in result.articles[0].provenance] == [
        "hatena",
        "official",
    ]
    strict = await organize(
        REQ.model_copy(update={"include_unknown_dates": False}), Source()
    )
    assert strict.articles[0].published_at == date(2026, 11, 6)


def test_normalization_keeps_resource_identity() -> None:
    assert (
        normalize_url(HttpUrl("https://EXAMPLE.com:443/a?x=1&utm_source=f#top"))
        == "https://example.com/a?x=1"
    )
    assert normalize_url(HttpUrl("http://example.com/a/")) != normalize_url(
        HttpUrl("https://example.com/a")
    )
    assert normalize_url(HttpUrl("https://example.com/a?x=1")) != normalize_url(
        HttpUrl("https://example.com/a?x=2")
    )


@pytest.mark.asyncio
async def test_cli_free_search_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    from backend import cli

    settings = Settings(_env_file=None, openai_api_key=None, news_sources=["qiita"])
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        cli.httpx,
        "AsyncClient",
        lambda: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )
    result = await cli.run(
        argparse.Namespace(
            command="search",
            query="AI",
            start=REQ.start_date,
            end=REQ.end_date,
            limit=5,
            sources=None,
        )
    )
    assert result.articles == []
    assert result.source_statuses[0].status == "ok"


@pytest.mark.asyncio
async def test_paid_post_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="http_503"):
            await OpenAINewsSource(
                client, SecretStr("test"), "test", paid_search_enabled=True
            ).search(REQ)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_invalid_input_prevents_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid input must not reach network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await search_news(
                " ",
                REQ.start_date,
                REQ.end_date,
                client=client,
                settings=Settings(_env_file=None),
            )

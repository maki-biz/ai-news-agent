"""Qiitaのページング・不正項目・部分失敗をモックで検証する。"""

from datetime import date

import httpx
import pytest

from backend.config import Settings
from backend.schemas import SearchRequest
from backend.tools.qiita import QiitaNewsSource

REQ = SearchRequest(
    query="AI", start_date=date(2026, 11, 1), end_date=date(2026, 11, 7)
)
ITEM = {
    "title": "AI",
    "url": "https://qiita.com/example/items/1",
    "created_at": "2026-11-06T23:00:00+09:00",
}


@pytest.mark.asyncio
async def test_qiita_validates_each_item() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "created:>=" in request.url.params["query"]
        assert "authorization" not in request.headers
        return httpx.Response(200, json=[ITEM, {"title": "bad"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await QiitaNewsSource(client, Settings(_env_file=None)).search(REQ)
    assert len(result.articles) == 1
    assert result.source_statuses[0].status == "partial"
    assert result.articles[0].published_at == date(2026, 11, 6)


@pytest.mark.asyncio
async def test_qiita_page_failure_preserves_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sleep(seconds: float) -> None:
        assert seconds >= 60

    monkeypatch.setattr("backend.tools.qiita.asyncio.sleep", sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=[ITEM] * 100)
            if request.url.params["page"] == "1"
            else httpx.Response(429)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await QiitaNewsSource(
            client, Settings(_env_file=None, source_max_candidates=200)
        ).search(REQ)
    assert len(result.articles) == 100
    assert "qiita: rate_limited" in result.warnings
    assert result.source_statuses[0].status == "partial"

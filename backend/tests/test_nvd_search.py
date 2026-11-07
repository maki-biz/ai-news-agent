"""NVDの期間分割・ページング・CVE分離の検証。"""

from datetime import date, datetime

import httpx
import pytest

from backend.config import Settings
from backend.schemas import SearchRequest
from backend.services.search import search_news
from backend.tools.cve import NvdNewsSource

CVE = {
    "id": "CVE-2026-12345",
    "sourceIdentifier": "example",
    "published": "2026-11-06T00:00:00",
    "lastModified": "2026-11-07T00:00:00",
    "vulnStatus": "Analyzed",
    "descriptions": [],
    "references": [],
}
REQ = SearchRequest(
    query="AI", start_date=date(2026, 11, 1), end_date=date(2026, 11, 7)
)


@pytest.mark.asyncio
async def test_nvd_pagination_and_cve_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(seconds: float) -> None:
        assert seconds >= 6

    monkeypatch.setattr("backend.tools.cve.asyncio.sleep", sleep)
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["startIndex"])
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "startIndex": offset,
                "resultsPerPage": 1,
                "totalResults": 2,
                "vulnerabilities": [{"cve": CVE}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await search_news(REQ, NvdNewsSource(client, Settings(_env_file=None)))
    assert offsets == [0, 1]
    assert result.articles == []
    assert len(result.vulnerabilities) == 1
    assert len(result.retrievals) == 2


@pytest.mark.asyncio
async def test_nvd_date_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("backend.tools.cve.asyncio.sleep", sleep)
    windows = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = datetime.fromisoformat(request.url.params["pubStartDate"])
        end = datetime.fromisoformat(request.url.params["pubEndDate"])
        assert (end - start).total_seconds() < 120 * 86400
        windows.append((start, end))
        return httpx.Response(
            200,
            json={
                "startIndex": 0,
                "resultsPerPage": 100,
                "totalResults": 0,
                "vulnerabilities": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NvdNewsSource(
            client, Settings(_env_file=None, source_max_pages=5)
        ).search(REQ.model_copy(update={"start_date": date(2026, 1, 1)}))
    assert len(windows) == 3
    assert windows[1][0] > windows[0][1]
    assert result.source_statuses[0].status == "ok"


@pytest.mark.asyncio
async def test_nvd_partial_page_and_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "startIndex": 0,
                "resultsPerPage": 2,
                "totalResults": 3,
                "vulnerabilities": [{"cve": CVE}, None],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NvdNewsSource(
            client, Settings(_env_file=None, source_max_pages=1)
        ).search(REQ)
    assert len(result.vulnerabilities) == 1
    assert result.source_statuses[0].status == "partial"
    assert "nvd: page_limit_reached" in result.warnings

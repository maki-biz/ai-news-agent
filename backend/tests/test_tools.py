import json
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from backend.tools.cve import get_cve_details
from backend.tools.http import ToolError
from backend.tools.search import search_news

CVE = {
    "id": "CVE-2021-44228",
    "sourceIdentifier": "nvd",
    "published": "2021-12-10T00:00:00",
    "lastModified": "2025-01-01T00:00:00",
    "vulnStatus": "Analyzed",
    "descriptions": [{"lang": "en", "value": "Example"}],
    "references": [],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload,code",
    [
        (200, {"totalResults": 1, "vulnerabilities": [{"cve": CVE}]}, None),
        (200, {"totalResults": 0, "vulnerabilities": []}, "cve_not_found"),
        (200, {}, "invalid_response"),
        (
            200,
            {"totalResults": 1, "vulnerabilities": [{"cve": {**CVE, "id": "wrong"}}]},
            "invalid_response",
        ),
        (429, {}, "rate_limited"),
        (403, {}, "http_403"),
        (500, {}, "http_500"),
        (302, {}, "http_302"),
    ],
)
async def test_cve(status, payload, code):
    def handler(request):
        assert request.url.params["cveId"] == "CVE-2021-44228"
        return httpx.Response(status, json=payload, headers={"Retry-After": "30"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if code:
            with pytest.raises(ToolError, match=code) as error:
                await get_cve_details("cve-2021-44228", client=client)
            if status == 429:
                assert error.value.retry_after == "30"
        else:
            assert (await get_cve_details("cve-2021-44228", client=client)).id == CVE[
                "id"
            ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ReadTimeout("secret"), "timeout"),
        (httpx.ConnectError("secret"), "connection_error"),
    ],
)
async def test_network(exception, code):
    def handler(request):
        raise exception

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match=code):
            await get_cve_details(CVE["id"], client=client)


@pytest.mark.asyncio
async def test_invalid_id():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await get_cve_details("bad", client=client)


def response(articles):
    return {
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {"type": "url", "url": f"https://example.com/{i}"}
                        for i in range(3)
                    ]
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"articles": articles}),
                        "annotations": [],
                    }
                ],
            },
        ],
    }


async def search(payload):
    def handler(request):
        body = json.loads(request.content)
        assert body["tool_choice"] == "required"
        assert body["max_tool_calls"] == 1
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await search_news(
            "AI",
            date(2026, 11, 1),
            date(2026, 11, 6),
            client=client,
            api_key=SecretStr("test"),
            model="test",
        )


@pytest.mark.asyncio
async def test_filter():
    result = await search(
        response(
            [
                {
                    "title": "Good",
                    "url": "https://example.com/0",
                    "published_at": "2026-11-02",
                },
                {
                    "title": "Duplicate",
                    "url": "https://example.com/0",
                    "published_at": "2026-11-02",
                },
                {
                    "title": "Unknown",
                    "url": "https://example.com/1",
                    "published_at": None,
                },
                {
                    "title": "Old",
                    "url": "https://example.com/2",
                    "published_at": "2025-01-01",
                },
                {
                    "title": "Invented",
                    "url": "https://example.com/3",
                    "published_at": None,
                },
            ]
        )
    )
    assert [a.title for a in result.articles] == ["Good", "Unknown"]
    assert all(a.verification_status == "unverified" for a in result.articles)
    assert result.articles[0].source == "example.com"
    assert len(result.warnings) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,code",
    [
        ({"status": "incomplete"}, "incomplete_response"),
        ({"status": "completed", "output": []}, "search_not_performed"),
        (
            response(
                [{"title": "Bad", "url": "javascript:alert(1)", "published_at": None}]
            ),
            "invalid_candidates",
        ),
        (
            response(
                [
                    {
                        "title": "Bad",
                        "url": "https://example.com",
                        "published_at": "yesterday",
                    }
                ]
            ),
            "invalid_candidates",
        ),
    ],
)
async def test_search_invalid(payload, code):
    with pytest.raises(ToolError, match=code):
        await search(payload)


@pytest.mark.asyncio
async def test_empty():
    assert (await search(response([]))).articles == []


def test_metrics_and_impact_conditions():
    from pydantic import ValidationError

    from backend.tools.cve import CveDetails

    payload = {
        **CVE,
        "metrics": {
            "cvssMetricV31": [
                {
                    "source": "nvd",
                    "type": "Primary",
                    "cvssData": {
                        "version": "3.1",
                        "vectorString": "CVSS:3.1/AV:N",
                        "baseScore": 10,
                    },
                }
            ],
            "ssvcV203": [{"source": "cisa", "ssvcData": {"version": "2.0.3"}}],
        },
        "configurations": [
            {
                "nodes": [
                    {
                        "operator": "OR",
                        "cpeMatch": [
                            {
                                "vulnerable": True,
                                "criteria": "cpe:2.3:a:example:product:*:*:*:*:*:*:*:*",
                                "versionEndExcluding": "2.0",
                            }
                        ],
                    }
                ]
            }
        ],
    }
    result = CveDetails.model_validate(payload)
    assert result.metrics.cvssMetricV31[0].cvssData.baseScore == 10
    assert "ssvcV203" in result.metrics.model_dump()
    assert result.configurations[0].nodes[0].cpeMatch[0].versionEndExcluding == "2.0"
    payload["metrics"]["cvssMetricV31"][0]["cvssData"]["baseScore"] = 11
    with pytest.raises(ValidationError):
        CveDetails.model_validate(payload)


@pytest.mark.asyncio
async def test_citation_only() -> None:
    """検索アクションの一覧が空でも本文の引用を根拠にできる。"""
    payload = response(
        [{"title": "Cited", "url": "https://example.com/0", "published_at": None}]
    )
    payload["output"][0]["action"]["sources"] = []
    payload["output"][1]["content"][0]["annotations"] = [
        {"type": "url_citation", "url": "https://example.com/0"}
    ]
    assert len((await search(payload)).articles) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {"status": "completed", "output": "bad"}])
async def test_invalid_envelope(payload: object) -> None:
    """API外側の構造不正は候補JSONの不正と区別する。"""
    with pytest.raises(ToolError, match="invalid_response"):
        await search(payload)

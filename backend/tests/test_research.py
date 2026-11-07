"""偽モデルと合成HTTP応答で実Runnerの境界を検証する。"""

import asyncio
import json
import logging
from datetime import date

import httpx
import pytest
from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from backend.config import Settings
from backend.research_schemas import ResearchRequest
from backend.services.research import research
from backend.tools.http import ToolError

REQUEST = ResearchRequest(
    query="AI", start_date=date(2026, 11, 1), end_date=date(2026, 11, 7)
)
RSS = b"""<rss version="2.0"><channel><title>Example</title>
<item><title>AI news</title><link>https://example.com/news</link>
<pubDate>Fri, 06 Nov 2026 00:00:00 GMT</pubDate><description>AI details</description></item>
</channel></rss>"""
CVE = {
    "id": "CVE-2026-12345",
    "sourceIdentifier": "example.com",
    "published": "2026-11-05T00:00:00",
    "lastModified": "2026-11-05T00:00:00",
    "vulnStatus": "Analyzed",
    "descriptions": [{"lang": "en", "value": "AI vulnerability"}],
    "references": [],
}


def settings(**changes):
    """個人設定と認証情報を使用しない設定を返す。"""
    return Settings(
        _env_file=None,
        news_sources=["rss"],
        rss_feeds=["https://example.com/feed"],
        **changes,
    )


def call(name="search_news", **arguments):
    """合成ツール要求を返す。"""
    return ResponseFunctionToolCall(
        type="function_call",
        name=name,
        arguments=json.dumps(arguments),
        call_id="call-1",
    )


def final(articles=None, vulnerabilities=None):
    """合成構造化出力を返す。"""
    return ResponseOutputMessage(
        id="message-1",
        role="assistant",
        status="completed",
        type="message",
        content=[
            ResponseOutputText(
                type="output_text",
                annotations=[],
                text=json.dumps(
                    {
                        "articles": articles or [],
                        "vulnerabilities": vulnerabilities or [],
                    }
                ),
            )
        ],
    )


class FakeModel(Model):
    """SDKのツール実行は置換せず、モデル応答だけを差し替える。"""

    def __init__(self, outputs, delay=0):
        self.outputs = iter(outputs)
        self.delay = delay
        self.calls = 0

    async def get_response(self, **kwargs):
        assert kwargs["model_settings"].max_tokens == 2000
        assert kwargs["model_settings"].parallel_tool_calls is False
        assert kwargs["tracing"].is_disabled()
        self.calls += 1
        await asyncio.sleep(self.delay)
        output = next(self.outputs)
        return ModelResponse(
            output=[output],
            usage=Usage(
                requests=1, input_tokens=100, output_tokens=20, total_tokens=120
            ),
            response_id=None,
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("streaming not expected")
        yield


async def execute(outputs, *, config=None, handler=None, delay=0):
    """通信先をモックし、実サービスを実行する。"""
    transport = httpx.MockTransport(
        handler or (lambda request: httpx.Response(200, content=RSS))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        return await research(
            REQUEST,
            settings=config or settings(),
            client=client,
            model=FakeModel(outputs, delay),
        )


@pytest.mark.asyncio
async def test_research_news_and_usage(caplog):
    with caplog.at_level(logging.INFO, logger="backend.search_usage"):
        result = await execute(
            [
                call(query="AI"),
                final([{"candidate_id": "article-1", "summary": "AI details"}]),
            ]
        )
    assert result.articles[0].title == "AI news"
    assert str(result.articles[0].url) == "https://example.com/news"
    assert result.articles[0].verification_status == "unverified"
    usage = json.loads(caplog.records[-1].message)
    assert usage["input_tokens"] == 200
    assert usage["llm_requests"] == 2
    assert usage["tool_calls"] == 1
    assert usage["estimated_standard_cost_usd"] == pytest.approx(0.00004)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("search_cves", {"query": "AI"}),
        ("get_cve_details", {"cve_id": "CVE-2026-12345"}),
    ],
)
async def test_cve_tools(name, args):
    def handler(request):
        assert request.url.host == "services.nvd.nist.gov"
        return httpx.Response(
            200,
            json={
                "totalResults": 1,
                "startIndex": 0,
                "resultsPerPage": 1,
                "vulnerabilities": [{"cve": CVE}],
            },
        )

    result = await execute(
        [
            call(name, **args),
            final(
                vulnerabilities=[
                    {"candidate_id": CVE["id"], "summary": "AI vulnerability"}
                ]
            ),
        ],
        handler=handler,
    )
    assert result.articles == []
    assert result.vulnerabilities[0].id == CVE["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection,code",
    [
        (
            {"candidate_id": "invented", "summary": "AI details"},
            "research_invalid_candidate",
        ),
        (
            {"candidate_id": "article-1", "summary": "invented fact"},
            "research_unsupported_summary",
        ),
    ],
)
async def test_reject_unsupported(selection, code):
    with pytest.raises(ToolError, match=code):
        await execute([call(query="AI"), final([selection])])


@pytest.mark.asyncio
async def test_requires_retrieval():
    with pytest.raises(ToolError, match="research_not_performed"):
        await execute([final()])


@pytest.mark.asyncio
async def test_tool_limit():
    second = call(query="AI")
    second.call_id = "call-2"
    with pytest.raises(ToolError, match="research_tool_limit"):
        await execute(
            [call(query="AI"), second], config=settings(research_max_tool_calls=1)
        )


@pytest.mark.asyncio
async def test_turn_limit():
    with pytest.raises(ToolError, match="research_turn_limit"):
        await execute([call(query="AI")], config=settings(research_max_turns=1))


@pytest.mark.asyncio
async def test_timeout():
    with pytest.raises(ToolError, match="research_timeout"):
        await execute([final()], config=settings(research_timeout=0.001), delay=0.02)


@pytest.mark.asyncio
async def test_source_failure():
    with pytest.raises(ToolError, match="all_sources_failed"):
        await execute([call(query="AI")], handler=lambda r: httpx.Response(403))


@pytest.mark.asyncio
async def test_empty_is_success():
    result = await execute([call(query="nonexistent"), final()])
    assert not result.articles


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "date_xml", ["", "<pubDate>Fri, 30 Oct 2026 00:00:00 GMT</pubDate>"]
)
async def test_unknown_or_outside_dates_are_excluded(date_xml):
    feed = RSS.replace(
        b"<pubDate>Fri, 06 Nov 2026 00:00:00 GMT</pubDate>", date_xml.encode()
    )
    with pytest.raises(ToolError, match="research_invalid_candidate"):
        await execute(
            [
                call(query="AI"),
                final([{"candidate_id": "article-1", "summary": "AI details"}]),
            ],
            handler=lambda r: httpx.Response(200, content=feed),
        )


@pytest.mark.asyncio
async def test_partial_source_failure():
    config = settings()
    config.rss_feeds.append("https://example.com/broken")
    result = await execute(
        [
            call(query="AI"),
            final([{"candidate_id": "article-1", "summary": "AI details"}]),
        ],
        config=config,
        handler=lambda r: (
            httpx.Response(403)
            if r.url.path == "/broken"
            else httpx.Response(200, content=RSS)
        ),
    )
    assert result.articles
    assert any(s.status == "failed" for s in result.source_statuses)
    assert any(s.status == "ok" for s in result.source_statuses)


@pytest.mark.asyncio
async def test_no_excerpt_is_explicit():
    result = await execute(
        [call(query="AI"), final([{"candidate_id": "article-1", "summary": ""}])],
        handler=lambda r: httpx.Response(
            200, content=RSS.replace(b"<description>AI details</description>", b"")
        ),
    )
    assert result.articles[0].summary is None
    assert result.articles[0].summary_basis == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,code", [(2, "research_invalid_candidate"), (6, "research_invalid_output")]
)
async def test_duplicate_and_excess_output(count, code):
    with pytest.raises(ToolError, match=code):
        await execute(
            [
                call(query="AI"),
                final([{"candidate_id": "article-1", "summary": "AI details"}] * count),
            ]
        )


@pytest.mark.asyncio
async def test_failed_run_logs_observed_usage_only(caplog):
    with (
        caplog.at_level(logging.INFO, logger="backend.search_usage"),
        pytest.raises(ToolError),
    ):
        await execute([call(query="AI")], config=settings(research_max_turns=1))
    usage = json.loads(caplog.records[-1].message)
    assert usage["input_tokens"] == 100
    assert usage["usage_complete"] is False
    assert usage["estimated_standard_cost_usd"] is None
    assert "AI details" not in caplog.text


@pytest.mark.asyncio
async def test_tool_timeout():
    async def handler(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, content=RSS)

    with pytest.raises(ToolError, match="research_timeout"):
        await execute(
            [call(query="AI")],
            config=settings(research_tool_timeout=0.001),
            handler=handler,
        )


@pytest.mark.asyncio
async def test_invalid_cve_id():
    with pytest.raises(ToolError, match="research_invalid_tool_input"):
        await execute([call("get_cve_details", cve_id="invalid")])


@pytest.mark.asyncio
async def test_missing_key_prevents_network():
    config = settings()
    config.openai_api_key = None
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("unexpected network"))
    ) as client:
        with pytest.raises(ToolError, match="missing_openai_api_key"):
            await research(REQUEST, settings=config, client=client)


def test_research_cli(monkeypatch, capsys):
    import sys

    from backend import cli
    from backend.research_schemas import ResearchResult

    async def fake(request, **kwargs):
        assert request.limit == 5
        assert request.include_unknown_dates is False
        return ResearchResult(
            query=request.query,
            articles=[],
            vulnerabilities=[],
            warnings=[],
            source_statuses=[],
        )

    monkeypatch.setattr(cli, "research", fake)
    monkeypatch.setattr(
        sys,
        "argv",
        ["backend", "research", "AI", "--start", "2026-11-01", "--end", "2026-11-07"],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["articles"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("include_usage", [True, False])
async def test_responses_http_contract(include_usage, caplog):
    from agents import OpenAIResponsesModel
    from openai import AsyncOpenAI

    caplog.set_level(logging.INFO, logger="backend.search_usage")
    outputs = iter(
        [
            call(query="AI"),
            final([{"candidate_id": "article-1", "summary": "AI details"}]),
        ]
    )
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_output_tokens"] == 2000
        assert payload["parallel_tool_calls"] is False
        assert payload["text"]["format"]["type"] == "json_schema"
        assert {t["name"] for t in payload["tools"]} == {
            "search_news",
            "search_cves",
            "get_cve_details",
        }
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(requests)}",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-6-luna",
                "output": [next(outputs).model_dump()],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                }
                if include_usage
                else None,
            },
        )

    async with (
        AsyncOpenAI(
            api_key="synthetic-test-key",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as sdk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=RSS))
        ) as client,
    ):
        result = await research(
            REQUEST,
            settings=settings(),
            client=client,
            model=OpenAIResponsesModel("gpt-6-luna", sdk),
        )
    assert len(requests) == 2
    assert result.articles[0].summary == "AI details"

    usage = json.loads(caplog.records[-1].message)
    assert usage["usage_complete"] is include_usage
    if not include_usage:
        assert usage["estimated_standard_cost_usd"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429])
async def test_model_http_failure_is_not_retried(status):
    from agents import OpenAIResponsesModel
    from openai import AsyncOpenAI

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "private-fixture-token"}}
        )

    async with (
        AsyncOpenAI(
            api_key="synthetic-test-key",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as sdk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("unexpected tool"))
        ) as client,
    ):
        with pytest.raises(ToolError, match="research_model_error") as error:
            await research(
                REQUEST,
                settings=settings(),
                client=client,
                model=OpenAIResponsesModel("gpt-6-luna", sdk),
            )
    assert len(calls) == 1
    assert "private-fixture-token" not in str(error.value)

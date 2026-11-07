"""調査実行、取得候補との照合、使用量の記録。"""

import asyncio
import json
import logging

import httpx
from agents import Model, OpenAIResponsesModel, RunConfig, RunContextWrapper, Runner
from agents.exceptions import AgentsException, MaxTurnsExceeded
from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from backend.agents.research import build_agent
from backend.config import Settings
from backend.research_schemas import (
    ResearchArticle,
    ResearchDraft,
    ResearchRequest,
    ResearchResult,
    ResearchVulnerability,
)
from backend.tools.http import ToolError
from backend.tools.research import ResearchContext, build_tools, cve_evidence
from backend.tools.usage import Usage, estimate_cost


def _summary(value: str, evidence: str) -> str | None:
    if not evidence:
        if value:
            raise ToolError("research_unsupported_summary")
        return None
    if not value.strip() or value not in evidence:
        raise ToolError("research_unsupported_summary")
    return value


def assemble_result(draft: ResearchDraft, context: ResearchContext) -> ResearchResult:
    """未取得ID、重複、根拠のない文章を拒否してメタデータを復元する。"""
    if not context.successes:
        raise ToolError("research_not_performed")
    if len(draft.articles) > context.request.limit:
        raise ToolError("research_article_limit")
    seen: set[str] = set()
    articles, vulnerabilities = [], []
    for selection in draft.articles:
        key = selection.candidate_id
        if key in seen or key not in context.articles:
            raise ToolError("research_invalid_candidate")
        seen.add(key)
        article = context.articles[key]
        summary = _summary(selection.summary, article.excerpt)
        articles.append(
            ResearchArticle(
                **article.model_dump(),
                summary=summary,
                summary_basis="excerpt" if summary else "unavailable",
            )
        )
    for selection in draft.vulnerabilities:
        key = selection.candidate_id
        if key in seen or key not in context.cves:
            raise ToolError("research_invalid_candidate")
        seen.add(key)
        cve = context.cves[key]
        summary = _summary(selection.summary, cve_evidence(cve))
        vulnerabilities.append(
            ResearchVulnerability(
                id=cve.id,
                url=f"https://nvd.nist.gov/vuln/detail/{cve.id}",
                published_at=cve.published.isoformat(),
                summary=summary,
                summary_basis="description" if summary else "unavailable",
            )
        )
    return ResearchResult(
        query=context.request.query,
        articles=articles,
        vulnerabilities=vulnerabilities,
        warnings=list(dict.fromkeys(context.warnings)),
        source_statuses=context.statuses,
    )


async def research(
    request: ResearchRequest,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    model: Model | None = None,
) -> ResearchResult:
    """単一Runnerを期限内で実行する。モデル注入で外部通信なしに検証できる。"""
    context = ResearchContext(request, settings, client)
    wrapper = RunContextWrapper(context=context)
    owned_client = None
    complete = False
    try:
        if model is None:
            if not settings.openai_api_key:
                raise ToolError("missing_openai_api_key")
            owned_client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                max_retries=0,
                timeout=settings.api_timeout_seconds,
            )
            model = OpenAIResponsesModel(settings.research_model, owned_client)
        async with asyncio.timeout(settings.research_timeout):
            result = await Runner.run(
                build_agent(model, build_tools(context), settings),
                request.model_dump_json(),
                context=wrapper,
                max_turns=settings.research_max_turns,
                run_config=RunConfig(
                    tracing_disabled=True, trace_include_sensitive_data=False
                ),
            )
            draft = ResearchDraft.model_validate(result.final_output)
            output = assemble_result(draft, context)
            complete = True
            return output
    except MaxTurnsExceeded:
        raise ToolError("research_turn_limit") from None
    except TimeoutError:
        raise ToolError("research_timeout") from None
    except (ValidationError, AgentsException):
        if context.failure is not None:
            raise context.failure from None
        raise ToolError("research_invalid_output") from None
    except OpenAIError:
        raise ToolError("research_model_error") from None
    finally:
        usage = wrapper.usage
        entries = usage.request_usage_entries
        costs = [
            estimate_cost(
                settings.research_model,
                Usage(
                    input_tokens=e.input_tokens,
                    output_tokens=e.output_tokens,
                    input_tokens_details={
                        "cached_tokens": e.input_tokens_details.cached_tokens
                    },
                ),
            )
            for e in entries
        ]
        usage_complete = bool(complete and entries and usage.requests == len(entries))
        logging.getLogger("backend.search_usage").info(
            json.dumps(
                {
                    "event": "research_usage",
                    "model": settings.research_model,
                    "llm_requests": usage.requests,
                    "tool_calls": context.calls,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cached_tokens": usage.input_tokens_details.cached_tokens,
                    "reasoning_tokens": usage.output_tokens_details.reasoning_tokens,
                    "usage_complete": usage_complete,
                    "estimated_standard_cost_usd": sum(costs)
                    if usage_complete and costs and all(c is not None for c in costs)
                    else None,
                    "pricing_date": "2026-11-07",
                    "estimate_basis": "standard token rates; observed usage only; not billing total",
                }
            )
        )
        if owned_client is not None:
            await owned_client.close()

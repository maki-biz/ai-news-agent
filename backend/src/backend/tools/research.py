"""既存取得処理を回数・期間制限付きFunction Toolとして公開する。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC
from zoneinfo import ZoneInfo

import httpx
from agents import FunctionTool, function_tool

from backend.config import Settings
from backend.cve_schemas import CveDetails
from backend.research_schemas import ResearchRequest
from backend.schemas import Article, SearchRequest, SourceStatus
from backend.services.search import CombinedSource
from backend.services.search import search_news as organize_news
from backend.services.urls import normalize_url
from backend.tools.cve import get_cve_details as fetch_cve
from backend.tools.http import ToolError
from backend.tools.search import build_sources


@dataclass
class ResearchContext:
    """実行単位の候補と上限。秘密情報はモデルへ渡さない。"""

    request: ResearchRequest
    settings: Settings
    client: httpx.AsyncClient
    failure: ToolError | None = None
    calls: int = 0
    successes: int = 0
    articles: dict[str, Article] = field(default_factory=dict)
    cves: dict[str, CveDetails] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    statuses: list[SourceStatus] = field(default_factory=list)

    def reserve(self) -> None:
        """通信前に失敗を含む呼び出し予算を消費する。"""
        if self.calls >= self.settings.research_max_tool_calls:
            raise ToolError("research_tool_limit")
        self.calls += 1

    def add_cve(self, cve: CveDetails) -> dict | None:
        """依頼期間に含まれるCVEだけを登録する。"""
        published = cve.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        if (
            not self.request.start_date
            <= published.astimezone(ZoneInfo(self.request.timezone)).date()
            <= self.request.end_date
        ):
            self.warnings.append("cve_outside_date_range")
            return None
        self.cves[cve.id] = cve
        return {"candidate_id": cve.id, "evidence": cve_evidence(cve)}


def cve_evidence(cve: CveDetails) -> str:
    """モデルと検証に同一の長さ制限付き説明を使用する。"""
    return "\n".join(d.value for d in cve.descriptions)[:2000]


def build_tools(context: ResearchContext) -> list[FunctionTool]:
    """既存の検索・NVD照会を再実装せずラップする。"""

    @asynccontextmanager
    async def boundary() -> AsyncIterator[None]:
        try:
            yield
        except ToolError as exc:
            context.failure = ToolError(exc.code, exc.retry_after)
            raise
        except TimeoutError:
            context.failure = ToolError("research_timeout")
            raise
        except ValueError:
            context.failure = ToolError("research_invalid_tool_input")
            raise

    async def search(query: str, sources: list[str]) -> dict:
        context.reserve()
        if not sources:
            raise ToolError("research_no_sources")
        request = SearchRequest(
            **(
                context.request.model_dump()
                | {"query": query, "limit": 10, "cve_limit": 10}
            )
        )
        async with asyncio.timeout(context.settings.research_tool_timeout):
            result = await organize_news(
                request,
                CombinedSource(
                    build_sources(context.client, context.settings, sources),
                    concurrency=context.settings.search_concurrency,
                    deadline=context.settings.source_deadline_seconds,
                ),
            )
        context.successes += 1
        context.warnings.extend(result.warnings)
        context.statuses.extend(result.source_statuses)
        candidates = []
        for article in result.articles:
            key = normalize_url(article.url)
            candidate_id = next(
                (k for k, a in context.articles.items() if normalize_url(a.url) == key),
                f"article-{len(context.articles) + 1}",
            )
            context.articles[candidate_id] = article
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "title": article.title[:500],
                    "published_at": str(article.published_at),
                    "evidence": article.excerpt,
                }
            )
        cves = [context.add_cve(cve) for cve in result.vulnerabilities]
        return {"articles": candidates, "vulnerabilities": [c for c in cves if c]}

    @function_tool(failure_error_function=None)
    async def search_news(query: str) -> dict:
        """Search news using concise topic keywords within the fixed request dates."""
        async with boundary():
            return await search(
                query, [s for s in context.settings.news_sources if s != "nvd"]
            )

    @function_tool(failure_error_function=None)
    async def search_cves(query: str) -> dict:
        """Search NVD by product or vulnerability keywords within the fixed dates."""
        async with boundary():
            return await search(query, ["nvd"])

    @function_tool(failure_error_function=None)
    async def get_cve_details(cve_id: str) -> dict:
        """Fetch details for an explicit CVE-YYYY-NNNN identifier."""
        async with boundary():
            context.reserve()
            async with asyncio.timeout(context.settings.research_tool_timeout):
                cve = await fetch_cve(
                    cve_id,
                    client=context.client,
                    timeout=context.settings.api_timeout_seconds,
                    api_key=context.settings.nvd_api_key,
                )
            context.successes += 1
            return {"vulnerability": context.add_cve(cve)}

    return [search_news, search_cves, get_cve_details]

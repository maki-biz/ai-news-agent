"""ソース障害を分離し、候補を決定的なルールで整理する。"""

import asyncio
from datetime import UTC, date
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from backend.schemas import SearchRequest, SearchResult, SourceResult, SourceStatus
from backend.services.urls import normalize_url
from backend.tools.http import ToolError


class NewsSource(Protocol):
    """取得方法に依存しない検索ソースの契約。"""

    async def search(self, request: SearchRequest) -> SourceResult:
        """候補を返す。取得失敗時はToolErrorを送出する。"""
        ...


class CombinedSource:
    """ソース別の期限と同時実行数を制限する。"""

    def __init__(
        self,
        sources: dict[str, NewsSource],
        *,
        concurrency: int = 3,
        deadline: float = 90,
    ) -> None:
        """固定されたソース集合を受け取る。"""
        if not sources or concurrency < 1 or deadline <= 0:
            raise ValueError("invalid sources or limits")
        self.sources, self.concurrency, self.deadline = sources, concurrency, deadline

    async def search(self, request: SearchRequest) -> SourceResult:
        """成功分を統合し、全ソース失敗は空の成功と区別する。"""
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run(name: str, source: NewsSource) -> SourceResult:
            try:
                async with semaphore:
                    async with asyncio.timeout(self.deadline):
                        result = await source.search(request)
                if not result.source_statuses:
                    result.source_statuses.append(
                        SourceStatus(source_id=name, status="ok")
                    )
                return result
            except (ToolError, TimeoutError) as exc:
                code = exc.code if isinstance(exc, ToolError) else "deadline_exceeded"
                return SourceResult(
                    articles=[],
                    source_urls=[],
                    warnings=[f"{name}: {code}"],
                    source_statuses=[
                        SourceStatus(
                            source_id=name,
                            status="failed",
                            error=code,
                            retry_after=exc.retry_after
                            if isinstance(exc, ToolError)
                            else None,
                        )
                    ],
                )

        results = await asyncio.gather(
            *(run(name, source) for name, source in self.sources.items())
        )
        statuses = [s for result in results for s in result.source_statuses]
        if all(s.status == "failed" for s in statuses):
            raise ToolError("all_sources_failed")
        return SourceResult(
            articles=[a for result in results for a in result.articles],
            vulnerabilities=[v for result in results for v in result.vulnerabilities],
            source_urls=list(
                dict.fromkeys(u for result in results for u in result.source_urls)
            ),
            warnings=[w for result in results for w in result.warnings],
            source_statuses=statuses,
            retrievals=[p for result in results for p in result.retrievals],
        )


async def search_news(request: SearchRequest, source: NewsSource) -> SearchResult:
    """期間検査・重複統合・順序固定を行う。公開日不明は設定で除外できる。"""
    result = await source.search(request)
    articles = {}
    warnings = [
        "Publication dates and article contents are not independently verified."
    ]
    warnings.extend(result.warnings)
    zone = ZoneInfo(request.timezone)
    for article in result.articles:
        published = article.published_at
        if article.published_datetime is not None:
            # タイムゾーンのない外部時刻をローカル時刻と推測しない。
            if article.published_datetime.tzinfo is None:
                published = None
            else:
                published = article.published_datetime.astimezone(zone).date()
        if published and not request.start_date <= published <= request.end_date:
            warnings.append(f"outside_date_range: {article.url}")
            continue
        if published is None:
            warnings.append(f"unknown_publication_date: {article.url}")
            if not request.include_unknown_dates:
                continue
        key = normalize_url(article.url)
        candidate = article.model_copy(
            update={
                "published_at": published,
                "source": urlsplit(str(article.url)).hostname or "",
            }
        )
        if key in articles:
            existing = articles[key]
            provenance = existing.provenance + [
                p for p in candidate.provenance if p not in existing.provenance
            ]
            if existing.published_at is None and candidate.published_at is not None:
                existing = candidate
            articles[key] = existing.model_copy(update={"provenance": provenance})
        else:
            articles[key] = candidate
    ordered = sorted(
        articles.values(),
        key=lambda a: (-(a.published_at or date.min).toordinal(), normalize_url(a.url)),
    )
    cves = {}
    for cve in result.vulnerabilities:
        # NVDのタイムゾーン省略時刻はAPI仕様に従いUTCとして扱う。
        published = (
            cve.published.replace(tzinfo=UTC)
            if cve.published.tzinfo is None
            else cve.published
        )
        if request.start_date <= published.astimezone(zone).date() <= request.end_date:
            previous = cves.get(cve.id)
            modified = (
                cve.lastModified.replace(tzinfo=UTC)
                if cve.lastModified.tzinfo is None
                else cve.lastModified
            )
            if previous is None:
                cves[cve.id] = cve
            else:
                old = (
                    previous.lastModified.replace(tzinfo=UTC)
                    if previous.lastModified.tzinfo is None
                    else previous.lastModified
                )
                if modified > old:
                    cves[cve.id] = cve
    ordered_cves = sorted(
        cves.values(),
        key=lambda c: (
            -(
                c.published.replace(tzinfo=UTC)
                if c.published.tzinfo is None
                else c.published
            ).timestamp(),
            c.id,
        ),
    )
    if len(ordered) > request.limit:
        warnings.append("article_limit_reached")
    if len(ordered_cves) > request.cve_limit:
        warnings.append("cve_limit_reached")
    return SearchResult(
        query=request.query,
        articles=ordered[: request.limit],
        vulnerabilities=ordered_cves[: request.cve_limit],
        source_urls=result.source_urls,
        warnings=list(dict.fromkeys(warnings)),
        source_statuses=result.source_statuses,
        retrievals=result.retrievals,
    )

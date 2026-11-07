"""RSS 1.0/2.0とAtomの共通取得。パーサーに通信を任せない。"""

import asyncio
import calendar
import re
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import httpx
from pydantic import ValidationError

from backend.config import Settings
from backend.schemas import (
    Article,
    Provenance,
    SearchRequest,
    SourceResult,
    SourceStatus,
)
from backend.tools.http import ToolError, request_response, validate_feed_url


def plain_excerpt(value: str) -> str:
    """外部HTMLを表示用命令として保持せず、短いテキストにする。"""
    return unescape(re.sub(r"<[^>]*>", " ", value))[:2000]


def parse_feed(
    content: bytes,
    url: str,
    source_id: str,
    request: SearchRequest,
    *,
    max_candidates: int = 100,
    publication_dates: bool = True,
) -> SourceResult:
    """公開日を更新日から推測せず、個別の不正項目は警告して除外する。"""
    parsed = feedparser.parse(content)
    if not parsed.version or (parsed.bozo and not parsed.entries):
        raise ToolError("invalid_feed")
    result = SourceResult(
        articles=[],
        source_urls=[url],
        warnings=[f"{source_id}: feed_history_not_guaranteed"],
    )
    if parsed.bozo:
        result.warnings.append(f"{source_id}: malformed_feed")
    if len(parsed.entries) > max_candidates:
        result.warnings.append(f"{source_id}: candidate_limit_reached")
    now = datetime.now(UTC)
    result.retrievals.append(
        Provenance(source_id=source_id, source_url=url, retrieved_at=now)
    )
    terms = request.query.casefold().split()
    for entry in parsed.entries[:max_candidates]:
        try:
            text = f"{entry.get('title', '')} {plain_excerpt(entry.get('summary', ''))}".casefold()
            if not any(term in text for term in terms):
                continue
            published, timestamp = None, None
            # feedparserのupdated→published互換フォールバックを避け、実キーだけを見る。
            if publication_dates and "published" in entry:
                raw = dict.get(entry, "published", "")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    published = date.fromisoformat(raw)
                elif (
                    "published_parsed" in entry
                    and dict.get(entry, "published_parsed")
                    and _has_timezone(raw)
                ):
                    timestamp = datetime.fromtimestamp(
                        calendar.timegm(dict.get(entry, "published_parsed")), UTC
                    )
                    published = timestamp.astimezone(ZoneInfo(request.timezone)).date()
            link = entry.get("link")
            if not link:
                raise ValueError
            result.articles.append(
                Article(
                    title=entry.get("title", ""),
                    url=urljoin(url, link),
                    published_at=published,
                    published_datetime=timestamp,
                    date_basis="published" if published else "unknown",
                    excerpt=plain_excerpt(entry.get("summary", "")),
                    provenance=[
                        Provenance(
                            source_id=source_id, source_url=url, retrieved_at=now
                        )
                    ],
                )
            )
        except (ValidationError, ValueError, TypeError, OverflowError):
            result.warnings.append(f"{source_id}: invalid_feed_entry")
    return result


def _has_timezone(value: str) -> bool:
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        try:
            return parsedate_to_datetime(value).tzinfo is not None
        except (ValueError, TypeError):
            return False


class RssNewsSource:
    """複数フィードを個別に取得する。本文リンクは自動巡回しない。"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        feeds: list[str] | None = None,
        source_id: str = "rss",
        publication_dates: bool = True,
    ) -> None:
        """運用者が設定したフィードと共通設定を受け取る。"""
        self.client, self.settings = client, settings
        self.feeds = [
            validate_feed_url(url)
            for url in (settings.rss_feeds if feeds is None else feeds)
        ]
        if not self.feeds:
            raise ValueError("empty feeds")
        self.source_id, self.publication_dates = source_id, publication_dates

    async def search(self, request: SearchRequest) -> SourceResult:
        """各フィードの成功・失敗を記録し、取得済み記事を保持する。"""
        result = SourceResult(articles=[], source_urls=[])
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.source_deadline_seconds * 0.95
        )
        for index, url in enumerate(self.feeds):
            name = f"{self.source_id}:{index}"
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ToolError("deadline_exceeded")
                async with asyncio.timeout(remaining):
                    response = await request_response(
                        self.client,
                        "GET",
                        url,
                        timeout=self.settings.api_timeout_seconds,
                        max_bytes=self.settings.max_response_bytes,
                        retries=1,
                        feed_redirects=True,
                    )
                fetched = parse_feed(
                    response.content,
                    str(response.url),
                    self.source_id,
                    request,
                    max_candidates=self.settings.source_max_candidates,
                    publication_dates=self.publication_dates,
                )
                result.articles.extend(fetched.articles)
                result.retrievals.extend(fetched.retrievals)
                result.source_urls.extend(fetched.source_urls)
                result.warnings.extend(fetched.warnings)
                partial = any(
                    "malformed_feed" in w
                    or "invalid_feed_entry" in w
                    or "limit_reached" in w
                    for w in fetched.warnings
                )
                result.source_statuses.append(
                    SourceStatus(source_id=name, status="partial" if partial else "ok")
                )
            except (ToolError, TimeoutError) as exc:
                if isinstance(exc, TimeoutError):
                    exc = ToolError("deadline_exceeded")
                result.warnings.append(f"{name}: {exc.code}")
                result.source_statuses.append(
                    SourceStatus(
                        source_id=name,
                        status="failed",
                        error=exc.code,
                        retry_after=exc.retry_after,
                    )
                )
        return result

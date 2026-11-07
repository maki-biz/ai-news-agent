"""Qiita公式APIの検索・ページング。"""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError

from backend.config import Settings
from backend.schemas import (
    Article,
    Provenance,
    SearchRequest,
    SourceResult,
    SourceStatus,
)
from backend.tools.http import ToolError, request_response
from backend.tools.rss import plain_excerpt

QIITA_URL = "https://qiita.com/api/v2/items"


class QiitaItem(BaseModel):
    """利用する公開記事のフィールド。"""

    title: str = Field(min_length=1)
    url: HttpUrl
    created_at: datetime
    body: str = ""


class QiitaNewsSource:
    """取得量を制限し、後続ページの失敗でも先行ページを保持する。"""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        """外部接続と秘密情報をモデル入力から分離する。"""
        self.client, self.settings = client, settings

    async def search(self, request: SearchRequest) -> SourceResult:
        """検索期間は広めに照会し、正確な日付境界は共通サービスで判定する。"""
        result = SourceResult(articles=[], source_urls=[])
        per_page = min(15, self.settings.source_max_candidates)
        # API側の日付解釈との差を吸収し、取得後に指定タイムゾーンで再検査する。
        start = (
            request.start_date - timedelta(days=1)
            if request.start_date.year > 1
            else request.start_date
        )
        end = (
            request.end_date + timedelta(days=1)
            if request.end_date.year < 9999
            else request.end_date
        )
        query = f"{request.query} created:>={start} created:<={end}"
        headers = (
            {
                "Authorization": f"Bearer {self.settings.qiita_access_token.get_secret_value()}"
            }
            if self.settings.qiita_access_token
            else {}
        )
        partial, completed = False, False
        error, retry_after = None, None
        try:
            async with asyncio.timeout(self.settings.source_deadline_seconds * 0.95):
                for page in range(1, self.settings.source_max_pages + 1):
                    if page > 1:
                        # 未認証枠は60回/時。単一実行内でもバーストを避ける。
                        await asyncio.sleep(3.7 if headers else 60.1)
                    response = await request_response(
                        self.client,
                        "GET",
                        QIITA_URL,
                        headers=headers,
                        params={"query": query, "page": page, "per_page": per_page},
                        timeout=self.settings.api_timeout_seconds,
                        max_bytes=self.settings.max_response_bytes,
                    )
                    try:
                        items = response.json()
                    except ValueError:
                        raise ToolError("invalid_response") from None
                    if not isinstance(items, list):
                        raise ToolError("invalid_response")
                    result.source_urls.append(str(response.url))
                    result.retrievals.append(
                        Provenance(
                            source_id="qiita",
                            source_url=str(response.url),
                            retrieved_at=datetime.now(UTC),
                        )
                    )
                    for item in items:
                        try:
                            parsed = QiitaItem.model_validate(item)
                            if parsed.created_at.tzinfo is None:
                                raise ValueError
                            result.articles.append(
                                Article(
                                    title=parsed.title,
                                    url=parsed.url,
                                    published_at=parsed.created_at.astimezone(
                                        ZoneInfo(request.timezone)
                                    ).date(),
                                    published_datetime=parsed.created_at,
                                    date_basis="published",
                                    excerpt=plain_excerpt(parsed.body),
                                    provenance=[
                                        Provenance(
                                            source_id="qiita",
                                            source_url=str(response.url),
                                            retrieved_at=datetime.now(UTC),
                                        )
                                    ],
                                )
                            )
                        except (ValidationError, ValueError):
                            partial = True
                            result.warnings.append("qiita: invalid_item")
                    if len(items) < per_page:
                        completed = True
                        break
                    if len(result.articles) >= self.settings.source_max_candidates:
                        break
                    if response.headers.get("rate-remaining") == "0":
                        result.warnings.append("qiita: rate_budget_exhausted")
                        break
        except (ToolError, TimeoutError) as exc:
            if not result.source_urls:
                if isinstance(exc, ToolError):
                    raise
                raise ToolError("deadline_exceeded") from None
            partial = True
            code = exc.code if isinstance(exc, ToolError) else "deadline_exceeded"
            error = code
            retry_after = exc.retry_after if isinstance(exc, ToolError) else None
            result.warnings.append(f"qiita: {code}")
        if not completed:
            partial = True
            result.warnings.append("qiita: incomplete_collection")
        result.articles = result.articles[: self.settings.source_max_candidates]
        result.source_statuses.append(
            SourceStatus(
                source_id="qiita",
                status="partial" if partial else "ok",
                error=error,
                retry_after=retry_after,
            )
        )
        return result

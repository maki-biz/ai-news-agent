"""検索ソース間で共有する入力・出力モデル。"""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, HttpUrl, model_validator

from backend.cve_schemas import CveDetails


class Provenance(BaseModel):
    """取得経路。元記事URLはArticle.urlとして別途保持する。"""

    source_id: str
    source_url: HttpUrl
    retrieved_at: datetime


class SourceStatus(BaseModel):
    """空の正常応答と取得失敗を区別する実行状態。"""

    source_id: str
    status: Literal["ok", "partial", "failed"]
    error: str | None = None
    retry_after: str | None = None


class SearchRequest(BaseModel):
    """検索条件。日付範囲は両端を含む。"""

    query: str = Field(min_length=1, max_length=2000)
    start_date: date
    end_date: date
    limit: int = Field(default=5, ge=1, le=10)
    cve_limit: int = Field(default=5, ge=1, le=100)
    timezone: str = "UTC"
    include_unknown_dates: bool = True

    @model_validator(mode="after")
    def validate_conditions(self) -> "SearchRequest":
        """通信前に空のクエリと逆転した期間を拒否する。"""
        if not self.query.strip() or self.start_date > self.end_date:
            raise ValueError("invalid search conditions")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("invalid timezone") from None
        return self


class Article(BaseModel):
    """公開日不明を許容する未検証の記事候補。"""

    title: str = Field(min_length=1)
    url: HttpUrl
    published_at: date | None
    source: str = ""
    verification_status: Literal["unverified"] = "unverified"
    published_datetime: datetime | None = None
    date_basis: Literal["published", "unknown"] = "unknown"
    provenance: list[Provenance] = Field(default_factory=list)
    excerpt: str = Field(default="", max_length=2000)


class Candidates(BaseModel):
    """検索が生成した記事候補JSON。"""

    articles: list[Article]


class SearchResult(BaseModel):
    """CLIと後続処理へ公開する検索結果。"""

    query: str
    articles: list[Article]
    source_urls: list[HttpUrl]
    warnings: list[str]
    vulnerabilities: list[CveDetails] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    retrievals: list[Provenance] = Field(default_factory=list)


class SourceResult(BaseModel):
    """ソース固有の根拠確認を終えた候補。内容の検証完了は意味しない。"""

    articles: list[Article]
    source_urls: list[HttpUrl]
    warnings: list[str] = Field(default_factory=list)
    vulnerabilities: list[CveDetails] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    retrievals: list[Provenance] = Field(default_factory=list)

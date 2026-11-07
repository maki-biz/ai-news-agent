"""検索ソース間で共有する入力・出力モデル。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class SearchRequest(BaseModel):
    """検索条件。日付範囲は両端を含む。"""

    query: str = Field(min_length=1, max_length=2000)
    start_date: date
    end_date: date
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def validate_conditions(self) -> "SearchRequest":
        """通信前に空のクエリと逆転した期間を拒否する。"""
        if not self.query.strip() or self.start_date > self.end_date:
            raise ValueError("invalid search conditions")
        return self


class Article(BaseModel):
    """公開日不明を許容する未検証の記事候補。"""

    title: str = Field(min_length=1)
    url: HttpUrl
    published_at: date | None
    source: str = ""
    verification_status: Literal["unverified"] = "unverified"


class Candidates(BaseModel):
    """検索が生成した記事候補JSON。"""

    articles: list[Article]


class SearchResult(BaseModel):
    """CLIと後続処理へ公開する検索結果。"""

    query: str
    articles: list[Article]
    source_urls: list[HttpUrl]
    warnings: list[str]


class SourceResult(BaseModel):
    """ソース固有の根拠確認を終えた候補。内容の検証完了は意味しない。"""

    articles: list[Article]
    source_urls: list[HttpUrl]
    warnings: list[str] = Field(default_factory=list)

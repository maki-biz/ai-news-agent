"""調査依頼、根拠付き選択、公開結果の型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import Article, SearchRequest, SourceStatus


class ResearchRequest(SearchRequest):
    """期間を固定した最大5件の調査依頼。"""

    limit: int = Field(default=5, ge=1, le=5)
    include_unknown_dates: bool = False


class Selection(BaseModel):
    """LLMは取得済みIDと原文中の抜粋のみを選ぶ。"""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    summary: str = Field(max_length=600)


class ResearchDraft(BaseModel):
    """メタデータを生成させない内部出力。"""

    model_config = ConfigDict(extra="forbid")
    articles: list[Selection] = Field(max_length=5)
    vulnerabilities: list[Selection] = Field(max_length=5)


class ResearchArticle(Article):
    """要約は取得文章から抽出し、独立検証とは区別する。"""

    summary: str | None
    summary_basis: Literal["excerpt", "unavailable"]


class ResearchVulnerability(BaseModel):
    """NVDから取得した説明を出典付きで示す。"""

    id: str
    url: str
    published_at: str
    source: Literal["NVD"] = "NVD"
    summary: str | None
    summary_basis: Literal["description", "unavailable"]
    verification_status: Literal["unverified"] = "unverified"


class ResearchResult(BaseModel):
    """CLIへ返す調査結果。"""

    query: str
    articles: list[ResearchArticle] = Field(max_length=5)
    vulnerabilities: list[ResearchVulnerability] = Field(max_length=5)
    warnings: list[str]
    source_statuses: list[SourceStatus]

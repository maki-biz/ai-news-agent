"""backend/を作業ディレクトリとして環境設定を読み込む。"""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """環境変数と作業ディレクトリの.envから読み込むサーバー設定。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"
    search_max_tool_calls: int = Field(default=1, ge=1, le=3)
    research_model: str = "gpt-6-luna"
    research_max_tool_calls: int = Field(default=3, ge=1, le=3)
    research_max_turns: int = Field(default=4, ge=1, le=4)
    research_max_output_tokens: int = Field(default=2000, ge=256, le=2000)
    research_tool_timeout: float = Field(default=90, gt=0, le=90)
    research_timeout: float = Field(default=180, gt=0, le=180)
    nvd_api_key: SecretStr | None = None
    api_timeout_seconds: float = Field(default=60, gt=0, le=180)
    paid_search_enabled: bool = False
    news_sources: list[Literal["rss", "hatena", "zenn", "qiita", "nvd"]] = Field(
        default_factory=lambda: ["rss", "hatena", "zenn", "qiita", "nvd"],
        min_length=1,
        max_length=5,
    )
    rss_feeds: list[str] = Field(
        default_factory=lambda: [
            "https://huggingface.co/blog/feed.xml",
            "https://www.jpcert.or.jp/rss/jpcert.rdf",
        ],
        max_length=20,
    )
    zenn_topics: list[str] = Field(
        default_factory=lambda: ["ai", "security"], max_length=10
    )
    qiita_access_token: SecretStr | None = None
    search_timezone: str = "UTC"
    include_unknown_dates: bool = True
    cve_limit: int = Field(default=5, ge=1, le=100)
    source_max_pages: int = Field(default=2, ge=1, le=10)
    source_max_candidates: int = Field(default=100, ge=1, le=1000)
    search_concurrency: int = Field(default=3, ge=1, le=5)
    source_deadline_seconds: float = Field(default=90, gt=0, le=300)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)

    @field_validator("search_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """外部通信前にタイムゾーンを検証する。"""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("invalid timezone") from None
        return value

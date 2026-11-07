"""既存CLI・ツール向け入口。通常収集に有料APIを使用しない。"""

from datetime import date

import httpx
from pydantic import SecretStr

from backend.config import Settings
from backend.schemas import SearchRequest, SearchResult
from backend.services.search import CombinedSource, NewsSource
from backend.services.search import search_news as organize_news
from backend.tools.cve import NvdNewsSource
from backend.tools.hatena import HatenaNewsSource
from backend.tools.http import ToolError
from backend.tools.openai_search import OpenAINewsSource
from backend.tools.qiita import QiitaNewsSource
from backend.tools.rss import RssNewsSource
from backend.tools.zenn import ZennNewsSource


def build_sources(
    client: httpx.AsyncClient, settings: Settings, sources: list[str] | None = None
) -> dict[str, NewsSource]:
    """許可した実装だけを明示的に生成する。OpenAIは明示選択時のみ。"""
    selected = settings.news_sources if sources is None else sources
    factories = {
        "rss": RssNewsSource,
        "hatena": HatenaNewsSource,
        "zenn": ZennNewsSource,
        "qiita": QiitaNewsSource,
        "nvd": NvdNewsSource,
    }
    if not selected or any(name not in {*factories, "openai"} for name in selected):
        raise ValueError("invalid sources")
    if "openai" in selected:
        if not settings.paid_search_enabled:
            raise ToolError("paid_search_disabled")
        if not settings.openai_api_key or not len(settings.openai_api_key):
            raise ToolError("missing_openai_api_key")
    result = {}
    for name in dict.fromkeys(selected):
        if name == "openai":
            result[name] = OpenAINewsSource(
                client,
                settings.openai_api_key,
                settings.openai_model,
                settings.api_timeout_seconds,
                settings.search_max_tool_calls,
                paid_search_enabled=settings.paid_search_enabled,
            )
        else:
            result[name] = factories[name](client, settings)
    return result


async def search_news(
    query: str,
    start_date: date,
    end_date: date,
    *,
    client: httpx.AsyncClient,
    api_key: SecretStr | None = None,
    model: str | None = None,
    limit: int = 5,
    timeout: float | None = None,
    max_tool_calls: int | None = None,
    settings: Settings | None = None,
    sources: list[str] | None = None,
) -> SearchResult:
    """既存の基本引数を維持し、設定と明示選択で無料・有料経路を分ける。"""
    settings = settings or Settings()
    overrides = {
        key: value
        for key, value in {
            "openai_api_key": api_key,
            "openai_model": model,
            "api_timeout_seconds": timeout,
            "search_max_tool_calls": max_tool_calls,
        }.items()
        if value is not None
    }
    settings = Settings(_env_file=None, **(settings.model_dump() | overrides))
    request = SearchRequest(
        query=query,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        cve_limit=settings.cve_limit,
        timezone=settings.search_timezone,
        include_unknown_dates=settings.include_unknown_dates,
    )
    selected = build_sources(client, settings, sources)
    # 明示的な単一有料検索では従来の詳細エラーコードを維持する。
    source = (
        selected["openai"]
        if list(selected) == ["openai"]
        else CombinedSource(
            selected,
            concurrency=settings.search_concurrency,
            deadline=settings.source_deadline_seconds,
        )
    )
    return await organize_news(request, source)

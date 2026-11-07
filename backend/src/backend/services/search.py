"""共通のニュース候補整理。"""

from typing import Protocol
from urllib.parse import urlsplit

from backend.schemas import SearchRequest, SearchResult, SourceResult


class NewsSource(Protocol):
    """取得方法に依存しない検索ソースの契約。"""

    async def search(self, request: SearchRequest) -> SourceResult:
        """根拠付きの候補を返す。通信失敗時はToolErrorを送出する。"""
        ...


async def search_news(request: SearchRequest, source: NewsSource) -> SearchResult:
    """候補の期間・重複を検査する。公開日不明の候補は警告付きで残す。

    Args:
        request: 検証済みの検索条件。
        source: 共通モデルを返す検索ソース。

    Returns:
        未検証であることを明示した検索結果。
    """
    result = await source.search(request)
    articles, seen = [], set()
    warnings = [
        "Publication dates and article contents are not independently verified."
    ]
    warnings.extend(result.warnings)
    for article in result.articles:
        if (
            article.published_at
            and not request.start_date <= article.published_at <= request.end_date
        ):
            warnings.append(f"outside_date_range: {article.url}")
            continue
        if article.url in seen:
            continue
        seen.add(article.url)
        if article.published_at is None:
            warnings.append(f"unknown_publication_date: {article.url}")
        articles.append(
            article.model_copy(
                update={"source": urlsplit(str(article.url)).hostname or ""}
            )
        )
    return SearchResult(
        query=request.query,
        articles=articles[: request.limit],
        source_urls=result.source_urls,
        warnings=warnings,
    )

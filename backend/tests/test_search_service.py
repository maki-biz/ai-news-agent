"""検索ソースの差し替えと共通ルールの検証。"""

from datetime import date

import pytest

from backend.schemas import Article, SearchRequest, SourceResult
from backend.services.search import search_news


@pytest.mark.asyncio
async def test_alternative_source() -> None:
    """OpenAIの引用形式に依存しないソースでも検索できる。"""

    class FeedSource:
        async def search(self, request: SearchRequest) -> SourceResult:
            assert request.query == "AI"
            article = Article(
                title="Example",
                url="https://example.com/news",
                published_at=date(2026, 11, 6),
            )
            return SourceResult(
                articles=[article, article], source_urls=["https://example.com/feed"]
            )

    result = await search_news(
        SearchRequest(
            query="AI", start_date=date(2026, 11, 6), end_date=date(2026, 11, 6)
        ),
        FeedSource(),
    )
    assert len(result.articles) == 1
    assert result.articles[0].source == "example.com"
    assert result.articles[0].verification_status == "unverified"


@pytest.mark.parametrize(
    "changes",
    [
        {"query": " "},
        {"query": "x" * 2001},
        {"limit": 0},
        {"limit": 11},
        {"start_date": date(2026, 11, 7)},
    ],
)
def test_search_conditions(changes: dict) -> None:
    """不正な条件は外部アクセス前に拒否する。"""
    values = {
        "query": "AI",
        "start_date": date(2026, 11, 1),
        "end_date": date(2026, 11, 6),
    }
    with pytest.raises(ValueError):
        SearchRequest(**(values | changes))

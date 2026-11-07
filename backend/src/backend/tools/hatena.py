"""はてなの注目記事を元記事URL付きで取得する。"""

import httpx

from backend.config import Settings
from backend.tools.rss import RssNewsSource


class HatenaNewsSource(RssNewsSource):
    """フィードの登録日時を元記事の公開日時と見なさない。"""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        """技術カテゴリの公式フィードを共通RSS処理へ渡す。"""
        super().__init__(
            client,
            settings,
            feeds=["https://b.hatena.ne.jp/hotentry/it.rss"],
            source_id="hatena",
            publication_dates=False,
        )

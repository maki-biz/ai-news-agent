"""Zenn公式トレンド・トピックフィード。"""

import re

import httpx

from backend.config import Settings
from backend.tools.rss import RssNewsSource


class ZennNewsSource(RssNewsSource):
    """非公式APIに依存しないZennアダプター。"""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        """設定されたトピックを固定ホスト上のフィードへ変換する。"""
        if any(
            not re.fullmatch(r"[a-zA-Z0-9_-]+", topic) for topic in settings.zenn_topics
        ):
            raise ValueError("invalid Zenn topic")
        feeds = ["https://zenn.dev/feed"] + [
            f"https://zenn.dev/topics/{topic}/feed" for topic in settings.zenn_topics
        ]
        super().__init__(client, settings, feeds=feeds, source_id="zenn")

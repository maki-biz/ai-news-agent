"""元URLを変更せず、重複判定用のURLを作る。"""

from urllib.parse import unquote_plus, urlsplit, urlunsplit

from pydantic import HttpUrl


def normalize_url(url: HttpUrl) -> str:
    """既知の追跡パラメーターとフラグメントだけを除く。"""
    parts = urlsplit(str(url))
    query = "&".join(
        item
        for item in parts.query.split("&")
        if not (
            unquote_plus(item.split("=", 1)[0]).lower().startswith("utm_")
            or unquote_plus(item.split("=", 1)[0]).lower() in {"fbclid", "gclid"}
        )
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

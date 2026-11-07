"""応答サイズと再試行を制限し、安全なエラーへ変換するHTTP処理。"""

import asyncio
import ipaddress
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx


class ToolError(Exception):
    """外部へ返せるエラー。ヘッダーや応答本文を含めない。"""

    def __init__(self, code: str, retry_after: str | None = None) -> None:
        """公開可能なコードと再試行情報を保持する。"""
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


def validate_feed_url(url: str) -> str:
    """運用設定のフィードURLを検査する。利用者の任意URL入力には使わない。"""
    try:
        parsed = httpx.URL(url)
        host = parsed.host.lower()
        if parsed.scheme not in {"https", "http"} or not host or parsed.userinfo:
            raise ValueError
        if (
            parsed.port not in {None, 80, 443}
            or host == "localhost"
            or host.endswith((".local", ".localhost"))
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        return str(parsed)
    except (ValueError, httpx.InvalidURL):
        raise ToolError("invalid_feed_url") from None


def _retry_delay(value: str | None) -> float:
    if value is None:
        return 1.0
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else 3.0
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 3.0


async def request_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int = 2_000_000,
    retries: int = 0,
    feed_redirects: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """サイズ制限付き取得。無料GETのみ短い待機で最大1回再試行する。"""
    attempts, redirects = 0, 0
    if feed_redirects:
        url = validate_feed_url(url)
    while True:
        try:
            async with client.stream(
                method, url, follow_redirects=False, **kwargs
            ) as response:
                if feed_redirects and response.status_code in {301, 302, 303, 307, 308}:
                    target = validate_feed_url(
                        str(response.url.join(response.headers.get("location", "")))
                    )
                    # 認証情報の転送や別ホストへの迂回を許可しない。
                    if (
                        redirects >= 3
                        or urlsplit(target).hostname != urlsplit(url).hostname
                        or (
                            urlsplit(url).scheme == "https"
                            and urlsplit(target).scheme != "https"
                        )
                    ):
                        raise ToolError("unsafe_redirect")
                    url, redirects = target, redirects + 1
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ToolError("response_too_large")
                    chunks.append(chunk)
                headers = dict(response.headers)
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=b"".join(chunks),
                    request=response.request,
                )
        except httpx.TimeoutException:
            raise ToolError("timeout") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raw_retry = exc.response.headers.get("retry-after")
            delay = _retry_delay(raw_retry)
            retry_after = None
            if status == 429 and raw_retry:
                try:
                    if raw_retry.isdigit():
                        retry_after = str(int(raw_retry))
                    else:
                        parsedate_to_datetime(raw_retry)
                        retry_after = str(int(delay))
                except (ValueError, TypeError, OverflowError):
                    pass
            if (
                method == "GET"
                and attempts < min(retries, 1)
                and status in {429, 502, 503, 504}
                and delay <= 2
            ):
                attempts += 1
                await asyncio.sleep(delay)
                continue
            raise ToolError(
                "rate_limited" if status == 429 else f"http_{status}", retry_after
            ) from None
        except httpx.RequestError:
            raise ToolError("connection_error") from None


async def request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    """JSONを取得する。有料POSTと既存単件照会は既定で再試行しない。"""
    response = await request_response(client, method, url, **kwargs)
    try:
        return response.json()
    except ValueError:
        raise ToolError("invalid_response") from None

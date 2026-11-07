"""秘密情報を含まないエラーへ変換する共通HTTP処理。"""

from typing import Any

import httpx


class ToolError(Exception):
    """外部へ返せるエラー。ヘッダーや応答本文を含めない。"""

    def __init__(self, code: str, retry_after: str | None = None) -> None:
        """公開可能なコードと再試行情報を保持する。"""
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


async def request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    """JSONを取得する。通信・HTTP・JSON解析失敗はToolErrorを送出する。"""
    try:
        response = await client.request(method, url, follow_redirects=False, **kwargs)
        response.raise_for_status()
    except httpx.TimeoutException:
        raise ToolError("timeout") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ToolError(
            "rate_limited" if status == 429 else f"http_{status}",
            exc.response.headers.get("retry-after") if status == 429 else None,
        ) from None
    except httpx.RequestError:
        raise ToolError("connection_error") from None
    try:
        return response.json()
    except ValueError:
        raise ToolError("invalid_response") from None

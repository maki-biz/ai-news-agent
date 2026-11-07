"""CLIのJSON契約と秘密情報の非公開を確認する。"""

import argparse
import json
import sys

import httpx
import pytest
from pydantic import SecretStr

from backend import cli
from backend.schemas import SearchResult
from backend.tools.cve import get_cve_details
from backend.tools.http import ToolError, request_json


def test_cli_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """成功JSONだけを標準出力へ返す。"""

    async def run(args: argparse.Namespace) -> SearchResult:
        return SearchResult(query="AI", articles=[], source_urls=[], warnings=[])

    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(sys, "argv", ["news", "cve", "CVE-2021-44228"])
    assert cli.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["articles"] == []
    assert captured.err == ""


@pytest.mark.parametrize(
    "error,exit_code,code",
    [
        (ToolError("rate_limited", "30"), 1, "rate_limited"),
        (ValueError("private-fixture-token"), 2, "invalid_input_or_configuration"),
    ],
)
def test_cli_error(
    error: Exception,
    exit_code: int,
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """内部の例外メッセージをJSONへ流さない。"""

    async def run(args: argparse.Namespace) -> SearchResult:
        raise error

    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(sys, "argv", ["news", "cve", "CVE-2021-44228"])
    assert cli.main() == exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == code
    assert "private-fixture-token" not in captured.err


@pytest.mark.asyncio
async def test_http_body_not_exposed() -> None:
    """ヘッダー・外部応答本文はエラーに含めない。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["apiKey"] == "private-fixture-token"
        return httpx.Response(403, text="private-fixture-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError) as error:
            await get_cve_details(
                "CVE-2021-44228",
                client=client,
                api_key=SecretStr("private-fixture-token"),
            )
    assert str(error.value) == "http_403"
    assert "private-fixture-token" not in repr(error.value.__dict__)


@pytest.mark.asyncio
async def test_invalid_json() -> None:
    """JSONでない本文は公開せず失敗する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="private-fixture-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="invalid_response"):
            await request_json(client, "GET", "https://example.com")

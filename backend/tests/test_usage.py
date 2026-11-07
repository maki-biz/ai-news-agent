"""使用量・コスト制御の検証。"""

import json
import logging

import pytest

from backend.config import Settings
from backend.tools.usage import Usage, report_usage


def test_cost_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """個人の.envに依存せず既定値を検査する。"""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("SEARCH_MAX_TOOL_CALLS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_model == "gpt-6-luna"
    assert settings.search_max_tool_calls == 1
    with pytest.raises(ValueError):
        Settings(_env_file=None, search_max_tool_calls=0)


def test_cost_and_reasoning(caplog: pytest.LogCaptureFixture) -> None:
    """キャッシュと推論トークンを二重加算しない。"""
    usage = Usage(
        input_tokens=1000,
        output_tokens=200,
        input_tokens_details={"cached_tokens": 100},
        output_tokens_details={"reasoning_tokens": 100},
    )
    with caplog.at_level(logging.INFO, logger="backend.search_usage"):
        report_usage("gpt-6-luna", usage, 1)
    result = json.loads(caplog.records[-1].message)
    assert result["estimated_standard_cost_usd"] == pytest.approx(0.010191)
    assert result["web_search_calls"] == 1


@pytest.mark.parametrize(
    "model,usage",
    [("unknown", Usage(input_tokens=10, output_tokens=10)), ("gpt-6-luna", None)],
)
def test_unknown_cost(
    model: str, usage: Usage | None, caplog: pytest.LogCaptureFixture
) -> None:
    """モデルや使用量が不明な場合に無料と誤表示しない。"""
    with caplog.at_level(logging.INFO, logger="backend.search_usage"):
        report_usage(model, usage, 1)
    assert json.loads(caplog.records[-1].message)["estimated_standard_cost_usd"] is None

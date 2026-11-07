"""検索使用量を本文・秘密情報なしで記録する。"""

import json
import logging
from decimal import Decimal

from pydantic import BaseModel, Field


class InputDetails(BaseModel):
    """課金計算で重複させないキャッシュ入力数。"""

    cached_tokens: int = Field(default=0, ge=0)


class OutputDetails(BaseModel):
    """出力トークンに内包される推論数。"""

    reasoning_tokens: int = Field(default=0, ge=0)


class Usage(BaseModel):
    """APIのトークン使用量。"""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    input_tokens_details: InputDetails = Field(default_factory=InputDetails)
    output_tokens_details: OutputDetails = Field(default_factory=OutputDetails)


def report_usage(model: str | None, usage: Usage | None, calls: int) -> None:
    """標準料金による参考概算を出力する。請求額や上限額ではない。

    Args:
        model: APIが返したモデル名。不明なモデルは概算を出さない。
        usage: API使用量。欠落時はゼロとみなさない。
        calls: 応答内のWeb Search呼び出し数。
    """
    estimate = estimate_cost(model, usage, calls)
    logging.getLogger("backend.search_usage").info(
        json.dumps(
            {
                "event": "search_usage",
                "model": model,
                "usage": usage.model_dump() if usage else None,
                "web_search_calls": calls,
                "estimated_standard_cost_usd": estimate,
                "pricing_date": "2026-11-07",
                "estimate_basis": "standard token rates + $0.01 per observed web_search_call; not billing total",
            }
        )
    )


def estimate_cost(
    model: str | None, usage: Usage | None, calls: int = 0
) -> float | None:
    """既存標準料金で概算する。不明な使用量・モデルはNone。"""
    # 2026-11-07公式標準料金。長文・特別tier等は計算対象外。
    rates = {
        "gpt-6-luna": ("0.10", "0.01", "0.50"),
        "gpt-6-astra": ("10", "1", "50"),
    }
    estimate = None
    if usage and model in rates and usage.input_tokens <= 272000:
        cached = usage.input_tokens_details.cached_tokens
        if cached <= usage.input_tokens:
            incoming, cache, outgoing = map(Decimal, rates[model])
            estimate = float(
                (
                    (usage.input_tokens - cached) * incoming
                    + cached * cache
                    + usage.output_tokens * outgoing
                )
                / Decimal(1000000)
                + calls * Decimal("0.01")
            )
    return estimate

"""検索・CVE取得をJSONで出力するCLI。"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date

import httpx

from backend.config import Settings
from backend.research_schemas import ResearchRequest, ResearchResult
from backend.schemas import SearchResult
from backend.services.research import research
from backend.tools.cve import CveDetails, get_cve_details
from backend.tools.http import ToolError
from backend.tools.search import search_news


async def run(args: argparse.Namespace) -> SearchResult | CveDetails | ResearchResult:
    """設定を読み、指定された取得処理を実行する。"""
    settings = Settings()
    async with httpx.AsyncClient() as client:
        if args.command == "research":
            return await research(
                ResearchRequest(
                    query=args.query,
                    start_date=args.start,
                    end_date=args.end,
                    limit=args.limit,
                    timezone=settings.search_timezone,
                ),
                settings=settings,
                client=client,
            )
        if args.command == "cve":
            return await get_cve_details(
                args.cve_id,
                client=client,
                timeout=settings.api_timeout_seconds,
                api_key=settings.nvd_api_key,
            )
        return await search_news(
            args.query,
            args.start,
            args.end,
            client=client,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            limit=args.limit,
            max_tool_calls=settings.search_max_tool_calls,
            timeout=settings.api_timeout_seconds,
            settings=settings,
            sources=getattr(args, "sources", None),
        )


def main() -> int:
    """成功時は0、取得失敗は1、入力・設定不正は2を返す。"""
    parser = argparse.ArgumentParser(description="Search news or retrieve NVD CVE data")
    commands = parser.add_subparsers(dest="command", required=True)
    cve = commands.add_parser("cve")
    cve.add_argument("cve_id")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--start", type=date.fromisoformat, required=True)
    search.add_argument("--end", type=date.fromisoformat, required=True)
    search.add_argument("--limit", type=int, default=5)
    search.add_argument(
        "--sources",
        nargs="+",
        choices=["rss", "hatena", "zenn", "qiita", "nvd", "openai"],
    )
    investigation = commands.add_parser("research")
    investigation.add_argument("query")
    investigation.add_argument("--start", type=date.fromisoformat, required=True)
    investigation.add_argument("--end", type=date.fromisoformat, required=True)
    investigation.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    # 使用量のみ標準エラーへ出し、結果JSONと通信ログを混ぜない。
    usage_logger = logging.getLogger("backend.search_usage")
    usage_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    usage_logger.addHandler(handler)
    usage_logger.propagate = False
    try:
        result = asyncio.run(run(args))
    except ToolError as exc:
        print(
            json.dumps({"error": exc.code, "retry_after": exc.retry_after}),
            file=sys.stderr,
        )
        return 1
    except ValueError:
        print(json.dumps({"error": "invalid_input_or_configuration"}), file=sys.stderr)
        return 2
    finally:
        usage_logger.removeHandler(handler)
        handler.close()
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

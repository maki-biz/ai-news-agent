"""NVDのCVE情報を取得し、利用するフィールドを検証する。"""

import asyncio
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from backend.config import Settings
from backend.cve_schemas import CveDetails, NvdResponse, Vulnerability
from backend.schemas import Provenance, SearchRequest, SourceResult, SourceStatus
from backend.tools.http import ToolError, request_json

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


async def get_cve_details(
    cve_id: str,
    *,
    client: httpx.AsyncClient,
    api_key: SecretStr | None = None,
    timeout: float = 60,
) -> CveDetails:
    """CVEを取得する。形式不正はValueError、取得失敗はToolErrorを送出する。"""
    cve_id = cve_id.strip().upper()
    if not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve_id):
        raise ValueError("CVE ID must have the form CVE-YYYY-NNNN")
    payload = await request_json(
        client,
        "GET",
        NVD_URL,
        params={"cveId": cve_id},
        headers={"apiKey": api_key.get_secret_value()} if api_key else {},
        timeout=timeout,
    )
    try:
        result = NvdResponse.model_validate(payload)
    except ValidationError:
        raise ToolError("invalid_response") from None
    if result.totalResults == 0 and not result.vulnerabilities:
        raise ToolError("cve_not_found")
    if result.totalResults != 1 or len(result.vulnerabilities) != 1:
        raise ToolError("invalid_response")
    cve = result.vulnerabilities[0].cve
    if cve.id != cve_id:
        raise ToolError("invalid_response")
    return cve


class NvdSearchResponse(BaseModel):
    """検索ページの構造を検証し、CVEごとの不正は分離する。"""

    totalResults: int = Field(ge=0)
    startIndex: int = Field(ge=0)
    resultsPerPage: int = Field(ge=0, le=2000)
    vulnerabilities: list[object]


class NvdNewsSource:
    """公開期間でCVEを取得する。記事モデルへ変換しない。"""

    def __init__(self, client: httpx.AsyncClient, settings: "Settings") -> None:
        """既存の単件照会と同じ接続設定を使用する。"""
        self.client, self.settings = client, settings

    async def search(self, request: "SearchRequest") -> "SourceResult":
        """120日未満の区間とページを上限制御しながら取得する。"""
        result = SourceResult(articles=[], source_urls=[])
        zone = ZoneInfo(request.timezone)
        try:
            start = datetime.combine(request.start_date, time.min, zone).astimezone(UTC)
            end = datetime.combine(request.end_date, time.max, zone).astimezone(UTC)
        except OverflowError:
            raise ToolError("unsupported_date_range") from None
        calls, partial, complete = 0, False, False
        error, retry_after = None, None
        headers = (
            {"apiKey": self.settings.nvd_api_key.get_secret_value()}
            if self.settings.nvd_api_key
            else {}
        )
        try:
            async with asyncio.timeout(self.settings.source_deadline_seconds * 0.95):
                while start <= end:
                    span = min(
                        end - start, timedelta(days=120) - timedelta(milliseconds=1)
                    )
                    stop = start + span
                    index = 0
                    while True:
                        if calls >= self.settings.source_max_pages:
                            raise ToolError("page_limit_reached")
                        if calls:
                            await asyncio.sleep(0.7 if headers else 6.1)
                        calls += 1
                        payload = await request_json(
                            self.client,
                            "GET",
                            NVD_URL,
                            params={
                                "pubStartDate": start.isoformat(
                                    timespec="milliseconds"
                                ),
                                "pubEndDate": stop.isoformat(timespec="milliseconds"),
                                "keywordSearch": request.query,
                                "startIndex": index,
                                "resultsPerPage": min(
                                    2000, self.settings.source_max_candidates
                                ),
                            },
                            headers=headers,
                            timeout=self.settings.api_timeout_seconds,
                            max_bytes=self.settings.max_response_bytes,
                        )
                        try:
                            page = NvdSearchResponse.model_validate(payload)
                            if (
                                page.startIndex != index
                                or len(page.vulnerabilities) > page.resultsPerPage
                                or (
                                    page.vulnerabilities
                                    and page.totalResults
                                    < index + len(page.vulnerabilities)
                                )
                            ):
                                raise ValueError
                        except (ValidationError, ValueError):
                            raise ToolError("invalid_response") from None
                        source_url = str(
                            httpx.URL(
                                NVD_URL,
                                params={
                                    "pubStartDate": start.isoformat(
                                        timespec="milliseconds"
                                    ),
                                    "pubEndDate": stop.isoformat(
                                        timespec="milliseconds"
                                    ),
                                    "keywordSearch": request.query,
                                    "startIndex": index,
                                    "resultsPerPage": min(
                                        2000, self.settings.source_max_candidates
                                    ),
                                },
                            )
                        )
                        result.source_urls.append(source_url)
                        result.retrievals.append(
                            Provenance(
                                source_id="nvd",
                                source_url=source_url,
                                retrieved_at=datetime.now(UTC),
                            )
                        )
                        for entry in page.vulnerabilities:
                            try:
                                cve = Vulnerability.model_validate(entry).cve
                                if not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve.id):
                                    raise ValueError
                                result.vulnerabilities.append(cve)
                                result.source_urls.append(
                                    f"https://nvd.nist.gov/vuln/detail/{cve.id}"
                                )
                            except (ValidationError, ValueError):
                                partial = True
                                result.warnings.append("nvd: invalid_item")
                        index += len(page.vulnerabilities)
                        if index >= page.totalResults:
                            break
                        if not page.vulnerabilities:
                            raise ToolError("invalid_pagination")
                        if (
                            len(result.vulnerabilities)
                            >= self.settings.source_max_candidates
                        ):
                            raise ToolError("candidate_limit_reached")
                    if stop >= end:
                        break
                    start = stop + timedelta(milliseconds=1)
                    if (
                        start <= end
                        and len(result.vulnerabilities)
                        >= self.settings.source_max_candidates
                    ):
                        raise ToolError("candidate_limit_reached")
                complete = True
        except (ToolError, TimeoutError) as exc:
            if not result.source_urls:
                if isinstance(exc, ToolError):
                    raise
                raise ToolError("deadline_exceeded") from None
            partial = True
            code = exc.code if isinstance(exc, ToolError) else "deadline_exceeded"
            error = code
            retry_after = exc.retry_after if isinstance(exc, ToolError) else None
            result.warnings.append(f"nvd: {code}")
        if not complete:
            result.warnings.append("nvd: incomplete_collection")
        result.vulnerabilities = result.vulnerabilities[
            : self.settings.source_max_candidates
        ]
        result.source_urls = list(dict.fromkeys(result.source_urls))
        result.source_statuses.append(
            SourceStatus(
                source_id="nvd",
                status="partial" if partial else "ok",
                error=error,
                retry_after=retry_after,
            )
        )
        return result

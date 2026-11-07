"""NVDのCVE情報を取得し、利用するフィールドを検証する。"""

import re
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError

from backend.tools.http import ToolError, request_json

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


class Description(BaseModel):
    """言語タグ付きの脆弱性説明。"""

    lang: str
    value: str


class Reference(BaseModel):
    """NVDが示す参照先と分類タグ。"""

    url: HttpUrl
    source: str | None = None
    tags: list[str] = Field(default_factory=list)


class CvssData(BaseModel):
    """CVSSのバージョン・ベクトル・基本スコア。"""

    version: str
    vectorString: str
    baseScore: float = Field(ge=0, le=10)
    baseSeverity: str | None = None


class Metric(BaseModel):
    """評価主体とCVSS評価の組。"""

    source: str
    type: str
    cvssData: CvssData
    baseSeverity: str | None = None


class Metrics(BaseModel):
    """CVSSの型検証と追加指標の保持。

    CVSS以外はmodel_extraに未検証のまま保持する。追加指標を判断に
    使用する際は専用のモデルで検証すること。JSON互換性のため同階層に出力する。
    """

    # 追加指標は未検証の拡張データとして保持し、CVSSの検証済み項目と区別する。
    model_config = ConfigDict(extra="allow")
    cvssMetricV2: list[Metric] = Field(default_factory=list)
    cvssMetricV30: list[Metric] = Field(default_factory=list)
    cvssMetricV31: list[Metric] = Field(default_factory=list)
    cvssMetricV40: list[Metric] = Field(default_factory=list)


class CpeMatch(BaseModel):
    """影響製品を表すCPEとバージョン境界。"""

    vulnerable: bool
    criteria: str
    versionStartIncluding: str | None = None
    versionStartExcluding: str | None = None
    versionEndIncluding: str | None = None
    versionEndExcluding: str | None = None


class Node(BaseModel):
    """製品条件の論理結合。"""

    operator: str
    negate: bool = False
    cpeMatch: list[CpeMatch] = Field(default_factory=list)


class Configuration(BaseModel):
    """影響条件のグループ。"""

    operator: str | None = None
    negate: bool = False
    nodes: list[Node]


class CveDetails(BaseModel):
    """NVDの脆弱性情報。修正版を境界値から推測しない。"""

    id: str
    sourceIdentifier: str
    published: datetime
    lastModified: datetime
    vulnStatus: str
    descriptions: list[Description]
    references: list[Reference]
    metrics: Metrics = Field(default_factory=Metrics)
    configurations: list[Configuration] = Field(default_factory=list)
    cisaExploitAdd: str | None = None
    cisaActionDue: str | None = None
    cisaRequiredAction: str | None = None
    cisaVulnerabilityName: str | None = None


class Vulnerability(BaseModel):
    """NVDの脆弱性エントリー。"""

    cve: CveDetails


class NvdResponse(BaseModel):
    """CVE照会レスポンスの件数とエントリー。"""

    totalResults: int = Field(ge=0)
    vulnerabilities: list[Vulnerability]


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

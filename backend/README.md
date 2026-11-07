# AI News Agent — Backend

## 環境構築

対応Pythonは`>=3.13.17,<3.14`です。開発環境は`.python-version`で3.13.17を指定し、uvを使用します。以下は`backend/`で実行してください。

```bash
uv sync --locked
cp .env.example .env
```

`.env`はGit管理対象外です。OpenAI APIキーは検索に使用し、ヘルスチェックとNVD取得には不要です。

## 起動

```bash
uv run uvicorn backend.main:app --reload --env-file .env
```

```bash
curl -i http://127.0.0.1:8000/api/health
```

HTTP 200と`{"status":"ok"}`が返れば起動成功です。
APIドキュメントは http://127.0.0.1:8000/docs で確認できます。
ヘルスチェックはプロセスの稼働確認であり、外部APIへの接続確認は行いません。

## 検証

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Step 2：検索・CVE取得CLI

`backend/`で実行します。検索には`.env`の`OPENAI_API_KEY`を設定してください。
モデルは`OPENAI_MODEL`で変更できます。

```bash
uv run python -m backend.cli cve CVE-2021-44228
uv run python -m backend.cli search "AIエージェント技術ニュース" --start 2026-10-31 --end 2026-11-06 --limit 5
```

成功時はJSONを標準出力へ、失敗時は安全なエラーコードを標準エラーへ出力します
（終了コード1、入力・設定エラーは2）。タイムアウトは既定60秒です。
429は`rate_limited`と`retry_after`を返し、自動リトライはしません。
再試行による課金や無制限の待機を避け、呼び出し元で再実行を判断します。

検索はResponses APIの`web_search`を必須指定します。候補URLはAPIの出典一覧・引用と照合し、
期間外と出典なしを除外、重複を排除します。公開日はモデルが抽出した値で独立検証は未実装です。
公開日不明はnullと警告で返し、全記事を`unverified`とします。URLはHTTP(S)形式を検証しますが、
リンク先へのアクセス・到達性検証は行いません。本文取得とSSRF対策は後続工程です。

NVDは公開日、更新日、説明、参照URL、CVSS、CPEの影響条件をPydanticで検証します。
CPEの上限バージョンを修正版と断定しません。SSVC等の追加指標も保持します。
CISA関連フィールドはNVDに含まれる場合のみ返し、KEVカタログとの直接照合は未実装です。

仕様参照：
- [OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [NVD API](https://nvd.nist.gov/developers/vulnerabilities)

## 検索ソースの追加

共通モデルは`src/backend/schemas.py`、共通処理は`services/search.py`にあります。
`NewsSource`の`search(request: SearchRequest) -> SourceResult`を実装して、
共通サービスの`search_news(request, source)`へ渡します。
各ソースは取得・外部形式の検証・根拠の保持を担当し、サービスは期間・重複・件数を扱います。
OpenAI固有の引用照合は`OpenAINewsSource`内にあり、RSS等へは強制しません。
実際のRSS取得や複数ソースの一括実行はまだ実装していません。

CLIの成功JSON形式は維持しています。検索APIの形式不正は`invalid_response`、
生成された候補JSONの不正は`invalid_candidates`で区別します。
APIキーは`SecretStr`として渡し、通信ヘッダー生成時のみ文字列へ展開します。
NVDの追加指標（SSVC等）は未検証の拡張データとして保持します。
CVSSと同じ型検証を通ったデータとしては扱いません。

2026-11-07：リファクタリング後のモックテスト33件成功。
実APIはこのリファクタリングでは呼び出していません。

## 検索コストの制御

既定は`OPENAI_MODEL=gpt-6-luna`、`SEARCH_MAX_TOOL_CALLS=1`です。
上限は1〜3で設定できます。記事件数の`--limit`とは別の制限です。
検索回数の制限で候補が減る可能性があり、費用の厳密な上限を保証しません。
出力トークン上限は4,000を維持し、JSONの途中切れを避けます。

検索APIが応答した後、使用モデル・入力/出力/推論トークン・Web Search呼び出し数・
標準料金による参考概算をJSONで標準エラーへ表示します。成功結果は従来通り標準出力です。
候補解析が失敗しても、使用量を取得できた場合は先に表示します。
通信失敗などで応答がない場合、使用量は取得できません。

概算は2026-11-07の標準単価と観測したWeb Search呼び出しあたり$0.01で計算します。
推論トークンは出力数に含まれるため二重加算しません。特別tier・地域料金・
キャッシュ書込等を反映する請求額ではありません。未知のモデルや使用量欠落時はnullです。
モデルのスナップショット名も未登録ならnullにし、推測で単価を適用しません。

- [公式料金](https://developers.openai.com/api/docs/pricing)
- [ツール回数・出力上限](https://developers.openai.com/api/reference/python/resources/responses/methods/create)

Step 2の実API検索はユーザーの実行結果で確認済みです。
低コスト設定への変更後の品質・実費は未計測です。

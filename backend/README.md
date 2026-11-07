# AI News Agent — Backend

## 環境構築・起動

Python `>=3.13.17,<3.14`（開発環境3.13.17）とuvを使用します。以下は`backend/`で実行します。

```bash
uv sync --locked
cp .env.example .env
uv run uvicorn backend.main:app --reload --env-file .env
```

`.env`は作業ディレクトリから読み込みます。既存の`.env`を設定例で上書きする必要はありません。
`GET /api/health`は外部サービスに依存せず`{"status":"ok"}`を返します。
検索・保存HTTP API、DB保存、分類・重要度評価は後続工程です。調査CLIは下記を参照してください。

## ニュース・CVE取得

通常の収集にOpenAI APIキーは不要です。

```bash
uv run python -m backend.cli search "AI agent" --start 2026-11-01 --end 2026-11-07 --limit 5
uv run python -m backend.cli search "脆弱性" --start 2026-11-01 --end 2026-11-07 --sources rss hatena zenn
uv run python -m backend.cli search "OpenSSL" --start 2026-11-01 --end 2026-11-07 --sources nvd
uv run python -m backend.cli cve CVE-2021-44228
```

成功時はJSONを標準出力、失敗時は安全なエラーコードを標準エラーへ出します。
終了コードは成功0、取得失敗1、入力・設定エラー2です。
一部ソースの失敗は取得済み結果と`warnings`・`source_statuses`を返し、終了コード0です。
全ソース失敗は`all_sources_failed`。正常に取得できたが該当0件の場合とは区別します。

既存の`query`・`articles`・`source_urls`・`warnings`と記事の公開日形式は維持します。
追加フィールドは`vulnerabilities`（CVE）、`source_statuses`（取得状態）、
`retrievals`（取得元URL・時刻）です。記事には`provenance`（複数の取得経路）、
`published_datetime`・`date_basis`・最大2,000文字の`excerpt`が加わります。
`--limit`は記事の件数（1〜10）、CVEは別設定`CVE_LIMIT`です。
記事は引き続き`unverified`であり、フィードから取得できたことは内容の検証完了を意味しません。

## ソースと検索の意味

| ソース | 取得方法 | 制約 |
| --- | --- | --- |
| `rss` | 公式RSS 1.0/RDF・RSS 2.0・Atom | 既定はHugging Face BlogとJPCERT/CC。配信に含まれる範囲のみ |
| `hatena` | 技術カテゴリの人気エントリーRSS | 登録日時を元記事公開日とは扱わない |
| `zenn` | トレンドと設定トピックの公式RSS | 既定トピックはai・security |
| `qiita` | 公式API v2 items | queryと作成日期間、ページング。認証任意 |
| `nvd` | 公式CVE API 2.0 | keywordSearchと公開日期間、ページング。認証任意 |

RSS系はタイトル・短い抜粋の大小文字を区別しない部分一致です。
空白区切りの語はOR条件です。日本語の形態素解析や自然言語の意図解釈は行いません。
`AIエージェント技術ニュースを調べて`ではなく`AI エージェント`のような語を指定してください。
Qiita・NVDはそれぞれの検索仕様に従うため、全ソースで検索意味は完全には一致しません。
意味的な関連性判定・日本語要約・分類・重要度評価は後続LLM層の責務です。

日付範囲は両端を含みます。時刻付き公開日は`SEARCH_TIMEZONE`（既定UTC）で日付化し、
日付のみの値はそのまま使用します。タイムゾーン不明の時刻、更新日時のみのフィード、
はてなの登録日時は公開日不明として扱います。NVDのタイムゾーン省略値はUTCです。
公開日不明は互換性のため警告付きで残し、`INCLUDE_UNKNOWN_DATES=false`で除外できます。
JPCERT/CCのRSSのdc:dateも、公開日と独立に確認できていないため更新日時として扱います。

同じ記事URLの既知の追跡パラメーター（utm_*・gclid・fbclid）とフラグメントを除いて
重複判定します。元記事URLと取得経路は保持します。http/https・末尾スラッシュ・その他の
クエリは統一しません。CVEは記事と混ぜずIDで重複排除します。
結果は公開日降順、同順位はURL/CVE IDで固定します。

RSSは過去期間の網羅を保証しません。ページ・候補上限や期限で取得が打ち切られた場合は
警告を返します。保存・キャッシュは未実装で、同じ入力でもライブソースの内容は変化します。
再現性を保証するのは固定fixtureに対する解析・整理処理です。

## 設定と通信上限

配列設定はJSON形式です。`.env.example`に全設定例があります。

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| `NEWS_SOURCES` | 5つの無料ソース | 通常収集対象。openaiは指定不可 |
| `RSS_FEEDS` | 公式フィード2件 | 運用者管理のHTTP(S) URL、最大20件 |
| `ZENN_TOPICS` | ai・security | 最大10トピック |
| `SOURCE_MAX_PAGES` | 2 | Qiita/NVDの1実行内の最大リクエスト数。NVD期間分割分も含む |
| `SOURCE_MAX_CANDIDATES` | 100 | APIごとの候補上限、RSSではフィードごとの走査上限 |
| `API_TIMEOUT_SECONDS` | 60 | HTTPタイムアウト |
| `SOURCE_DEADLINE_SECONDS` | 90 | ソース全体の期限。待機時間も含む |
| `SEARCH_CONCURRENCY` | 3 | 同時実行するソース数 |
| `MAX_RESPONSE_BYTES` | 2,000,000 | 解凍後の応答サイズ上限 |
| `CVE_LIMIT` | 5 | 出力CVE数（1〜100） |

Qiitaは1ページ最大15件を取得します（候補上限が15未満ならその件数）。
未認証60回/時/IP、認証あり1,000回/時の枠を前提とし、後続ページまで
60.1秒／3.7秒待ちます。NVDはキーなし5回/30秒、あり50回/30秒の枠を前提とし、
後続リクエストまで6.1秒／0.7秒待ちます。NVDの期間は120日未満に分割します。
これらは単一実行内の間隔制御です。別プロセス・他アプリ・共有IPの使用量は管理しません。
429や期限切れでは停止して部分結果を返します。長期間の完全収集用途ではありません。

RSSの無料GETのみ429/502/503/504で最大1回再試行します。
Retry-Afterが2秒を超える場合は待ち続けず終了します。Qiita・NVDと有料POSTは自動再試行しません。
フィードは運用者が管理する設定のみを使用します。明白なローカルURL・認証付きURLを拒否し、
リダイレクトは同一ホスト、最大3回、HTTPSからHTTPへの変更禁止です。
DNSリバインディング対策を備えた任意URL取得サービスではありません。
記事リンク先の本文は自動取得せず、外部記事・API応答全文はファイルへ保存しません。

## 任意の有料追加調査

OpenAI API・既存Web Search実装・使用量記録は維持しています。
通常収集の失敗や0件を理由に有料検索へ切り替えることはありません。
キーの設定だけでも有効になりません。

有料追加調査を行う場合のみ、運用者が`PAID_SEARCH_ENABLED=true`と
`OPENAI_API_KEY`を設定したうえで明示選択します。

```bash
uv run python -m backend.cli search "AI agents" --start 2026-11-01 --end 2026-11-07 --sources openai
```

無効時はアダプターの直接呼び出しも`paid_search_disabled`で通信前に拒否します。
`OPENAI_MODEL`・`SEARCH_MAX_TOOL_CALLS`（1〜3、既定1）は従来どおりです。
トークン使用量・Web Search回数・参考概算は標準エラーに出し、本文・秘密情報は出しません。
概算は請求額や厳密な費用上限ではありません。単価表は従来のものを維持しています。
後続LLM層でも、有料検索許可は要約等のLLM利用許可と分けて扱ってください。

## 拡張・検証

`NewsSource.search(SearchRequest) -> SourceResult`を実装して`build_sources()`へ登録します。
外部アクセスと外部形式の検証は`tools/`、期間・URL正規化・重複統合は`services/`です。
LLMやDB保存を取得アダプターへ混ぜません。CVEモデルは`cve_schemas.py`にあり、
`tools.cve.CveDetails`のimportと`cve`コマンドのJSON形式は維持しています。

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

自動テストは外部APIをMockTransportで置き換えます。固定の合成RSS・CVE・記事データを使用し、
ライブ疎通確認とは分離します。有料APIの実通信は自動テストで行いません。

公式仕様：
- [Qiita API v2](https://qiita.com/api/v2/docs)
- [Zenn RSS](https://zenn.dev/zenn/articles/zenn-feed-rss)
- [JPCERT/CC RSS](https://www.jpcert.or.jp/rss/)
- [NVD API](https://nvd.nist.gov/developers/vulnerabilities)
- [NVD利用制限](https://nvd.nist.gov/developers/start-here)

2026-11-07の確認：モックテスト66件成功。5種類すべての無料取得経路を
APIキーなし・APIは1ページ・候補上限10件で疎通確認し、統合結果の記事5件・CVE5件を取得しました。
上限による部分取得は状態・警告に記録されています。有料APIは呼び出していません。
取得した外部本文・応答全文は保存していません。


## Step 3：単一調査エージェント

`research`はOpenAI Agents SDKで依頼を解釈し、ニュース検索・NVD期間検索・CVE ID照会の
3つのFunction Toolを選びます。既存の取得・期間判定・重複整理を再利用しています。
サーバー側の`OPENAI_API_KEY`が必要です。依存追加はありません。

```bash
# backend/で実行。結果は標準出力、使用量は標準エラー。
uv run python -m backend.cli research "AIエージェントの技術動向と関連する脆弱性" \
  --start 2026-11-01 --end 2026-11-07 --limit 5
```

`research`の実行は有料LLMを呼び出します。既存の`search`・`cve`の挙動は維持しています。
`PAID_SEARCH_ENABLED`とは別であり、調査Agentには有料Web Searchを公開しません。
ニュース取得は`NEWS_SOURCES`のうちNVD以外、CVE検索はNVDを使用します。
フィードが提供しない過去記事や、設定ソースに存在しない記事は取得できません。

出力は`query`、`articles`、`vulnerabilities`、`warnings`、`source_statuses`です。
記事は最大5件（`--limit`は1〜5）、CVEは別枠で最大5件です。
記事には`title`、`url`、`published_at`、`source`、`summary`、`verification_status`、
`summary_basis`、既存の取得経路等を含めます。CVEはNVD詳細ページを出典として返します。
日付範囲は両端を含み、`SEARCH_TIMEZONE`で判定します。調査CLIでは公開日不明を除外します。
CVE ID照会も指定公開期間に入るものだけを結果候補にします。

LLMは候補IDと最大600文字の**抽出要約**を選択します。抜粋・NVD説明内の連続した原文と
一致することをコードで検査し、タイトル・URL・日付は取得済み値から復元します。
翻訳・言い換えは行いません。抜粋がなければ`summary: null`、`summary_basis: "unavailable"`。
抽出できた場合は記事が`excerpt`、CVEが`description`です。
取得情報自体の真偽や抜粋による文脈の欠落までは独立検証できないため、すべて`unverified`です。
記事リンク先の本文取得、DB、RAG、Web UI、複数Agentは追加していません。

| 調査設定 | 既定値（最大値） |
| --- | --- |
| `RESEARCH_MODEL` | gpt-6-luna |
| `RESEARCH_MAX_TOOL_CALLS` | 3（3） |
| `RESEARCH_MAX_TURNS` | 4（4、LLM呼び出し回数） |
| `RESEARCH_MAX_OUTPUT_TOKENS` | 2,000（2,000、1回のLLM応答） |
| `RESEARCH_TOOL_TIMEOUT` | 90秒（90） |
| `RESEARCH_TIMEOUT` | 180秒（180、調査全体） |

並列ツール呼び出しは無効にし、通信前に回数を数えます。LLM通信の自動再試行はありません。
取得側の既存ページ数・通信サイズ・期限・無料GET再試行制限も適用されます。
1回のツール呼び出しが複数HTTP通信を伴うことがあります。モデルへ渡す候補はツールごとに
記事10件・CVE10件まで、本文抜粋は各2,000文字までです。

一部ソース失敗は取得済み結果と警告を返します。正常な0件は空配列で成功します。
全ソース失敗やCVE照会失敗は即終了し、別ツールへの自動回復は行いません。
上限到達は`research_tool_limit` / `research_turn_limit`、期限超過は`research_timeout`、
未取得候補・重複は`research_invalid_candidate`、根拠のない要約は`research_unsupported_summary`。
LLM通信失敗は`research_model_error`、不正な構造化出力は`research_invalid_output`です。
取得なしの最終出力は`research_not_performed`。エラー時は標準出力に不完全な結果を出しません。
CLI終了コードは既存と同じ成功0、実行失敗1、入力・設定エラー2です。

標準エラーの`research_usage`イベントに、取得できたLLM入力・キャッシュ入力・出力・推論
トークン数、LLM応答数、ツール回数、標準料金概算を記録します。推論は出力の内数です。
概算はリクエストごとに計算して合算し、Function ToolにはWeb Search料金を加算しません。
不明なモデル、使用量なし、失敗で使用量が不完全な場合は総費用を`null`とします。
失敗時にも取得済みトークン数を残し、`usage_complete: false`で未計測分と区別します。
これは請求額や厳密な費用上限ではありません。特別tier等の料金は対象外です。
SDKトレース送信は無効です。外部応答全文や秘密情報をログ・ファイルへ保存しません。

料金表は[OpenAI公式料金](https://developers.openai.com/api/docs/pricing)の
2026-11-07確認値を使用しています。

自動テストでは実Runnerと偽モデル、Responses API・ニュース・NVDのモックHTTPを組み合わせ、
正常系、根拠照合、期間制限、部分失敗、上限・期限、使用量、既存CLIとの互換性を確認します。
実モデルの検索語選択・関連性判断・ライブソースの網羅性はモックでは評価できません。
今回、有料APIによる実通信確認は実施していません。

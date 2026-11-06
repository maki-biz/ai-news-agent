# AI News Agent — Backend

## 環境構築

Python 3.13以上とuvを使用します。以下は`backend/`で実行してください。

```bash
uv sync --locked
cp .env.example .env
```

`.env`はGit管理対象外です。APIキーは今後のニュース調査機能で使用する予定で、ヘルスチェックには不要です。

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

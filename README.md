# asken-record-audit

あすけんの食事記録を、食事の写真と照合して監査する常駐アプリケーションです。監査の対象は記録内容で、写真は照合の材料です。

現時点はプロジェクトの土台のみで、取得・照合・保存の機能は未実装です。サイクルでは起動ログを 1 行出すだけです。

## 実行形態

コンテナを起動し続けるだけで定期実行されます。スケジューリングはコンテナ内の `main.py` 自身が無限ループで行うため、cron・タイマー・ホスト側の定期実行など、外からコールする仕組みは使いません。

- 既定では 1 日に 1 回相当 (`INTERVAL_SECONDS=86400`) の間隔でサイクルを実行します。
- 1 サイクルの例外はログに残して次のサイクルへ進み、プロセスは落ちません。
- `SIGTERM` / `SIGINT` を受け取ると、実行中のサイクルを終えてから終了します。待機中であれば、その場で終了します。
- `RUN_ONCE=1` を指定すると、1 サイクルだけ実行して終了します。

## 設定 (環境変数)

`.env.example` を `.env` にコピーして値を設定します。`.env` はコミットしません。

| 変数 | 既定値 | 説明 |
|---|---|---|
| `ASKEN_USERNAME` | (必須) | あすけんのログインメールアドレス |
| `ASKEN_PASSWORD` | (必須) | あすけんのログインパスワード |
| `AZURE_OPENAI_ENDPOINT` | (必須) | Azure OpenAI のエンドポイント |
| `AZURE_OPENAI_API_KEY` | (必須) | Azure OpenAI の API キー |
| `AZURE_OPENAI_DEPLOYMENT` | (必須) | 一次照合に使うデプロイ名 |
| `AZURE_OPENAI_VERIFY_DEPLOYMENT` | (必須) | 差分が大きい食事を裏取りするデプロイ名 |
| `INTERVAL_SECONDS` | `86400` | サイクルの間隔 (秒)。Dockerfile の `ENV` で設定済みで、通常は指定不要 |
| `RUN_ONCE` | (未設定) | `1` / `true` / `yes` を指定すると 1 サイクルのみ実行して終了 (動作確認用) |

あすけんと Azure OpenAI の変数は、土台の段階では読み込みません。機能の実装時に使用します。`INTERVAL_SECONDS` を上書きしたい場合のみ、`docker run` の `-e` や Compose の `environment` で渡してください。

## セットアップ

```bash
cp .env.example .env
mkdir -p data
docker compose up --build -d
docker compose logs -f
```

コンテナは非 root ユーザー (uid 1000) で動作します。`./data` をバインドマウントするため、ホスト側のディレクトリは uid 1000 が書き込める権限にしてください。uid が異なる場合は `--user "$(id -u):$(id -g)"` を付けて実行します。

### 動作確認 (1 サイクルだけ実行して終了)

```bash
docker build -t asken-record-audit .
docker run --rm \
  --env-file .env \
  -e RUN_ONCE=1 \
  -v "$(pwd)/data:/data" \
  asken-record-audit
```

## 開発

```bash
pip install -r requirements-dev.txt
flake8
pytest
```

## ライセンス

[MIT License](./LICENSE)

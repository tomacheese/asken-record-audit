# asken-record-audit

あすけんの食事記録を、食事の写真と照合して監査するバッチです。監査の対象は記録内容で、写真は照合の材料です。

現時点はプロジェクトの土台のみで、取得・照合・保存の機能は未実装です。起動してログを 1 行出して終了します。

## 設定 (環境変数)

`.env.example` を `.env` にコピーして値を設定します。`.env` はコミットしません。

| 変数 | 説明 |
|---|---|
| `ASKEN_USERNAME` | あすけんのログインメールアドレス |
| `ASKEN_PASSWORD` | あすけんのログインパスワード |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI のエンドポイント |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI の API キー |
| `AZURE_OPENAI_DEPLOYMENT` | 一次照合に使うデプロイ名 |
| `AZURE_OPENAI_VERIFY_DEPLOYMENT` | 差分が大きい食事を裏取りするデプロイ名 |

土台の段階ではいずれの変数も読み込みません。機能の実装時に使用します。

## セットアップ

```bash
cp .env.example .env
mkdir -p data
docker compose up --build
```

コンテナは非 root ユーザー (uid 1000) で動作します。`./data` をバインドマウントするため、ホスト側のディレクトリは uid 1000 が書き込める権限にしてください。uid が異なる場合は `--user "$(id -u):$(id -g)"` を付けて実行します。

## 開発

```bash
pip install -r requirements-dev.txt
flake8
pytest
```

## ライセンス

[MIT License](./LICENSE)

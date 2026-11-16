# AGENTS.md

あすけんの食事記録を、食事の写真と照合して監査する Python 製の常駐アプリケーションです。あすけん (asken.jp) からの取得、Azure OpenAI の Luna による照合 (差異が大きい場合は Terra で再検証)、結果の保存 (`data/<date>/audit.json`) まで実装済みです。

## 言語ルール

- コミットの説明、PR / Issue のタイトルと本文、コードコメント・docstring、ドキュメントは日本語で書く。日本語と英数字の間には半角スペースを入れる。
- 例外: ログ・エラーメッセージ、引用、技術的な識別子は元の言語のまま。
- Conventional Commits を使い、type / scope は英語、description は日本語にする。
- ワークフロー (`.github/workflows/docker.yml`) の承認依頼コメント本文とその `body-includes` の一致キーは、互いに一致している必要があるため変更しない。

## 開発コマンド

- 依存インストール: `pip install -r requirements-dev.txt` (Python 3.15 想定。Dockerfile のベースイメージは `python:3.15-slim`)
- Lint: `flake8` (設定は `.flake8`)
- テスト: `pytest` (`pytest.ini` で `src` を import パスに追加している)
- 起動: `docker compose up --build -d` (`.env` が必要。`.env.example` を参照)
- 1 サイクルだけ動作確認: `RUN_ONCE=1` を付けて実行する (詳細は `README.md`)。

## アーキテクチャ

- `src/main.py`: `Config.from_env()` による設定読み込み、`target_dates`、1 サイクルを担う `run_cycle(config, client, azure)`、`INTERVAL_SECONDS` / `RUN_ONCE` / `TARGET_DATE` を使う常駐ループ、SIGTERM / SIGINT による停止を持つ。スケジューリングは cron ではなくこのプロセスのループで行い、外からコールする仕組みは持たない。
- `src/asken_client.py`: `requests` で asken.jp にログインし、`fetch_meal` で食事記録を、`download_photo` で写真を取得する。
- `src/storage.py`: `write_json_atomic` (一時ファイルに書いてから replace) と `read_json` を持つ。
- `src/fetch.py`: `fetch_day` が 4 種類の食事と写真をまずメモリ上にすべて取得し、成功したときだけ `data/<date>/record.json` と `photos/` を all-or-nothing で保存する。
- `src/azure_client.py`: `AzureClient.chat_json` が Azure OpenAI の chat completions を strict な JSON スキーマで呼び出す。接続エラー・タイムアウト・429・5xx は最大 3 回まで再試行する。
- `src/audit.py`: `audit_day` が保存済みの `record.json` と `photos/` だけを読み、写真のある食事を主デプロイメントで照合する。`needs_check` を除いた推定差が `VERIFY_THRESHOLD_KCAL` 以上のときは検証用デプロイメントで再検証し、`data/<date>/audit.json` をアトミックに書く。入力のハッシュが変わらなければ前回の結果を再利用する。
- 取得と照合は、保存したファイル (`record.json`、`photos/`) だけでつながっている。そのため、保存済みのデータに対して照合をやり直せる。
- 設定は環境変数で受け取る。秘匿情報でない変数の既定値は Dockerfile の `ENV` に置き、`Config.from_env()` では必須として読む (コード側で二重に既定値を持たない)。任意なのは `RUN_ONCE` と `TARGET_DATE` だけである。

## 守るべき不変条件

- `.env` と `data/` はコミットしない。
- 認証情報の値をログ・README・`.env.example` に出さない。
- 1 サイクルの例外で常駐プロセスを落とさない。ログに残して次のサイクルへ進む。
- `.env.example` には秘匿情報と接続先だけを置く。`INTERVAL_SECONDS` のような既定値のある設定は、Dockerfile の `ENV` を正とし、`.env.example` には足さない。
- 元の入力データ (`record.json` と写真) は照合で書き換えない。差異は `audit.json` に別に持つ。
- `AZURE_OPENAI_ENDPOINT` の実際の値 (内部の接続先) をリポジトリ、Issue、PR に書かない。README などにはプレースホルダーを使う。
- コンテナは非 root (uid 1000) で動作する。書き込み先 (`/data`) の権限を変える場合は `README.md` の説明も更新する。

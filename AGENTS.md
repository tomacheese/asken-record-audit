# AGENTS.md

あすけんの食事記録を、食事の写真と照合して監査する Python 製のバッチです。現時点は土台のみで、機能は未実装です。

## 言語ルール

- コミットの説明、PR / Issue のタイトルと本文、コードコメント・docstring、ドキュメントは日本語で書く。日本語と英数字の間には半角スペースを入れる。
- 例外: ログ・エラーメッセージ、引用、技術的な識別子は元の言語のまま。
- Conventional Commits を使い、type / scope は英語、description は日本語にする。
- ワークフロー (`.github/workflows/docker.yml`) の承認依頼コメント本文とその `body-includes` の一致キーは、互いに一致している必要があるため変更しない。

## 開発コマンド

- 依存インストール: `pip install -r requirements-dev.txt` (Python 3.15 想定。Dockerfile のベースイメージは `python:3.15-slim`)
- Lint: `flake8` (設定は `.flake8`)
- テスト: `pytest` (`pytest.ini` で `src` を import パスに追加している)
- 起動: `docker compose up --build` (`.env` が必要。`.env.example` を参照)

## アーキテクチャ

- `src/main.py`: 起動してログを出すだけの骨組み。取得・照合・保存は今後ここから呼び出す。

## 守るべき不変条件

- `.env` と `data/` はコミットしない。
- 認証情報の値をログ・README・`.env.example` に出さない。
- コンテナは非 root (uid 1000) で動作する。書き込み先 (`/data`) の権限を変える場合は `README.md` の説明も更新する。

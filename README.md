# asken-record-audit

あすけんの食事記録を、食事の写真と照合して監査する常駐アプリケーションです。監査の対象は記録内容で、写真は照合の材料です。

あすけんにログインして直近の食事記録と写真を取得して保存し、保存済みのデータを Azure OpenAI で写真と照合して、差異を `data/<日付>/audit.json` に残します。検出した差異であすけん側の入力を書き換えることはなく、Discord などへの通知や外部連携もしません。

## 仕組み

取得と照合は分離しており、保存済みのファイル (`record.json` と `photos/`) だけを介して結びつきます。

1. 取得: あすけんにログインし、直近の食事記録 (品目名、数量、エネルギー) と食事写真を、食事区分 (朝食・昼食・夕食・間食) ごとに取得して保存します。取得に失敗した日や、記録が後から修正された日が残らないように、毎サイクル直近 `ASKEN_TRACK_DAYS` 日分を保存し直します。4 区分すべての取得に成功したときだけ保存し、一部が失敗したときは既存の保存済みデータを変更しません。
2. 照合: 写真がある食事区分だけを対象に、写真と入力済み品目の一覧を一次照合用のモデル (既定は GPT-6 Luna) に渡し、構造化出力 (JSON スキーマ) で差異を受け取ります。推定の差分が大きい食事は、上位のモデル (既定は GPT-6.1 Terra) で、同じ入力から独立に再照合して裏取りします。写真がない食事は対象外です。
3. 保存: 結果を `data/<日付>/audit.json` に保存します。元の入力データ (`record.json` と写真) は書き換えません。

入力 (品目、写真、モデル名、しきい値) が前回と同じ食事は、API を呼ばずに前回の結果を引き継ぎます。失敗した区分と、裏取りに失敗した区分は、次のサイクルでやり直します。

## 実行形態

コンテナを起動し続けるだけで定期実行されます。スケジューリングはコンテナ内の `main.py` 自身が無限ループで行うため、cron・タイマー・ホスト側の定期実行など、外からコールする仕組みは使いません。

- 既定では 1 日に 1 回相当 (`INTERVAL_SECONDS=86400`) の間隔でサイクルを実行します。
- 1 サイクルの例外はログに残して次のサイクルへ進み、プロセスは落ちません。日ごと、食事区分ごとの失敗も他の処理に影響しません。
- `SIGTERM` / `SIGINT` を受け取ると、実行中のサイクルを終えてから終了します。待機中であれば、その場で終了します。
- `RUN_ONCE=1` を指定すると、1 サイクルだけ実行して終了します。

## 設定 (環境変数)

`.env.example` を `.env` にコピーして値を設定します。`.env` はコミットしません。

| 変数 | 既定値 | 説明 |
|---|---|---|
| `ASKEN_USERNAME` | (必須) | あすけんのログインメールアドレス |
| `ASKEN_PASSWORD` | (必須) | あすけんのログインパスワード |
| `AZURE_OPENAI_ENDPOINT` | (必須) | Azure OpenAI のエンドポイント。`/openai/v1` までを含むベース URL (`https://<resource>.openai.azure.com/openai/v1`) |
| `AZURE_OPENAI_API_KEY` | (必須) | Azure OpenAI の API キー |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-6-luna` | 一次照合に使うデプロイ名 |
| `AZURE_OPENAI_VERIFY_DEPLOYMENT` | `gpt-6.1-terra` | 差分が大きい食事を裏取りするデプロイ名 |
| `VERIFY_THRESHOLD_KCAL` | `150` | 裏取りに回す推定差分のしきい値 (kcal)。0 以上の整数 |
| `ASKEN_TRACK_DAYS` | `3` | 取得と照合の対象日数 (今日を含む直近 N 日)。1 以上の整数 |
| `INTERVAL_SECONDS` | `86400` | サイクルの間隔 (秒) |
| `RUN_ONCE` | (未設定) | `1` / `true` / `yes` を指定すると 1 サイクルのみ実行して終了 |
| `TARGET_DATE` | (未設定) | `YYYY-MM-DD`。指定すると、その 1 日だけを対象にする (`ASKEN_TRACK_DAYS` より優先)。空文字は未設定として扱う |

`.env` に書くのは秘匿情報と接続先だけです。それ以外の変数の既定値は `Dockerfile` の `ENV` にあり、上書きしたい場合のみ、`docker run` の `-e` や Compose の `environment` で渡してください。必須の変数が未設定、または整数や日付が不正なときは、起動時にエラーで終了します。

### 裏取りのしきい値 (`VERIFY_THRESHOLD_KCAL`)

一次照合の結果のうち、`needs_check` を除く差異の `kcal_delta_max` の合計 (上限側の推定差分) がしきい値以上の食事を、裏取りに回します。

- 既定の 150 kcal は、試作で裏取りまで確認できた大きな差異 (+180 〜 +330 kcal) を取りこぼさないように、それより低く置いた値です。
- `needs_check` (写真だけでは判定できない飲み物や汁物) は常に出やすく、数 kcal から 150 kcal 程度の幅で出るため、判定から除いています。
- 一次照合の試作 39 食のうち、上限側の推定差分が 150 kcal 以上だったのは 17 食 (44%)、180 kcal 以上は 13 食 (33%) でした。150 kcal にしても、すべての食事が裏取りに回ることはなく、上位モデルの呼び出しを抑えられます。
- 上位モデルの呼び出しを減らしたいときは値を上げ、取りこぼしを減らしたいときは下げてください。

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

### 過去の日付を指定して 1 回だけ実行 (動作確認・バックフィル)

`RUN_ONCE=1` と `TARGET_DATE` を組み合わせると、指定した 1 日だけを取得して照合し、終了します。モデルやプロンプトを変えたあとに、過去分を再照合するときにも使えます。範囲の指定はできないため、複数日は日付を変えて繰り返してください。

```bash
docker run --rm \
  --env-file .env \
  -e RUN_ONCE=1 \
  -e TARGET_DATE=2026-10-23 \
  -v "$(pwd)/data:/data" \
  asken-record-audit
```

常駐 (`RUN_ONCE` なし) のコンテナに `TARGET_DATE` を指定すると、毎サイクルその日だけを処理し続けるため、通常の運用では指定しません。

## 保存されるデータ

`data/<日付>/` に次のファイルを保存します。`data/` はコミットしません。

| ファイル | 内容 |
|---|---|
| `record.json` | 取得した生データ。食事区分ごとの品目 (品目名、数量、エネルギー) と写真のファイル名。照合では書き換えません |
| `photos/<食事区分>_<連番>.jpg` | 食事の写真 |
| `audit.json` | 照合結果 |

### `audit.json` の形式

```json
{
  "date": "2026-10-23",
  "audited_at": "2026-10-24T08:00:00+09:00",
  "models": {"primary": "gpt-6-luna", "verify": "gpt-6.1-terra"},
  "verify_threshold_kcal": 150,
  "meals": {
    "lunch": {
      "status": "ok",
      "input_hash": "<sha256>",
      "verified": true,
      "result": {
        "visible_items": [
          {"name": "コーンスープ", "count": 1, "amount_note": "カップ 1 杯", "confidence": 0.9}
        ],
        "diffs": [
          {
            "type": "not_visible",
            "item": "コーンスープ",
            "detail": "付け合わせはコーンで、コーンスープは写真にない",
            "kcal_delta_min": -164,
            "kcal_delta_max": -120,
            "confidence": 0.8
          }
        ],
        "summary": "..."
      },
      "primary": {"visible_items": [], "diffs": [], "summary": "..."},
      "estimated_kcal_delta": {"min": -164, "max": -120},
      "verification_error": null
    },
    "dinner": {"status": "error", "input_hash": "<sha256>", "error": "<メッセージ>"}
  }
}
```

- `meals` には、写真がある食事区分だけが入ります。
- `status` は `ok` または `error` です。`error` の区分は `error` にメッセージを持ち、次のサイクルでやり直します。
- `result` が採用した結果です。裏取りに成功したときは裏取りのモデルの結果で、`primary` に一次照合の結果を残し、`verified` が `true` になります。裏取りに回さなかった、または失敗したときは一次照合の結果で、`verified` が `false`、`primary` はありません。裏取りに失敗したときは `verification_error` にメッセージが入り、次のサイクルでやり直します。
- `diffs[].type` は次の 5 種類です。
  - `missing_in_input`: 写真にあるが未入力
  - `not_visible`: 入力にあるが写真にない
  - `count_mismatch`: 個数の差異
  - `amount_mismatch`: 量の差異
  - `needs_check`: 写真だけでは判定できない (飲み物・汁物など)
- `kcal_delta_min` / `kcal_delta_max` は、差分の範囲 (最小〜最大) です。正の値は入力が写真より少ないことを表します。写真から見えないものは推測せず、`confidence` (0〜1) で確信度を示します。
- `estimated_kcal_delta` は、`needs_check` を除く差異の `kcal_delta_min` と `kcal_delta_max` の合計です。これは推定であり、記録の実績ではありません。

## 開発

```bash
pip install -r requirements-dev.txt
flake8
pytest
```

テストは Azure OpenAI とあすけんへの通信をモックしており、API キーやあすけんの認証情報は不要です。

## ライセンス

[MIT License](./LICENSE)

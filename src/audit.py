"""保存済みの食事記録と写真を照合し、差異を `audit.json` に保存する。

あすけんには依存せず、`record.json` と `photos/` だけを入力にする。
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
from pathlib import Path

from asken_client import MEAL_TYPES
from azure_client import AzureClient, AzureError
from storage import read_json, write_json_atomic

logger = logging.getLogger("asken-record-audit")

# プロンプトやスキーマを変えたら上げる。入力ハッシュが変わり、保存済みの結果が再照合される。
PROMPT_VERSION = 1

DIFF_TYPES = [
    "missing_in_input",
    "not_visible",
    "count_mismatch",
    "amount_mismatch",
    "needs_check",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "visible_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": ["integer", "null"]},
                    "amount_note": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["name", "count", "amount_note", "confidence"],
                "additionalProperties": False,
            },
        },
        "diffs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": DIFF_TYPES},
                    "item": {"type": "string"},
                    "detail": {"type": "string"},
                    "kcal_delta_min": {"type": "integer"},
                    "kcal_delta_max": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "type",
                    "item",
                    "detail",
                    "kcal_delta_min",
                    "kcal_delta_max",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["visible_items", "diffs", "summary"],
    "additionalProperties": False,
}

PROMPT_HEAD = (
    "あなたは食事記録の照合担当です。添付の食事写真と、記録済みの品目リストを比較し、差異を抽出してください。\n"
    "ルール:\n"
    "- 写真で確認できることだけを根拠にし、見えないものを推測しない。\n"
    "- 飲み物・汁物は写真の外にある場合があるため、写真に無いだけでは誤りと断定せず type=needs_check にする。\n"
    "- 量の差異は kcal_delta_min/max の範囲で出す。\n"
    "- 写真にある品目は visible_items に個数と量の目安つきで列挙する。\n"
    "- visible_items と diffs の各項目に、0 から 1 の confidence を付ける。\n"
    "\n"
    "記録済みの品目リスト:\n"
)

_VISIBLE_ITEM_KEYS = ["amount_note", "confidence", "count", "name"]
_DIFF_KEYS = [
    "confidence",
    "detail",
    "item",
    "kcal_delta_max",
    "kcal_delta_min",
    "type",
]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_confidence(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
    )


def _require_keys(obj: object, keys: list[str], where: str) -> None:
    if not isinstance(obj, dict):
        raise ValueError(
            f"{where} must be an object, got {type(obj).__name__}"
        )
    if sorted(obj) != keys:
        raise ValueError(f"{where} must have keys {keys}, got {sorted(obj)}")


def validate_result(result: dict) -> None:
    """モデルの応答がスキーマに沿っているか検証し、違反なら ValueError を送出する。"""
    _require_keys(result, ["diffs", "summary", "visible_items"], "result")
    if not isinstance(result["summary"], str):
        raise ValueError("summary must be a string")
    if not isinstance(result["visible_items"], list):
        raise ValueError("visible_items must be a list")
    if not isinstance(result["diffs"], list):
        raise ValueError("diffs must be a list")

    for i, vi in enumerate(result["visible_items"]):
        where = f"visible_items[{i}]"
        _require_keys(vi, _VISIBLE_ITEM_KEYS, where)
        if not isinstance(vi["name"], str):
            raise ValueError(f"{where}.name must be a string")
        if vi["count"] is not None and not _is_int(vi["count"]):
            raise ValueError(f"{where}.count must be an integer or null")
        if not isinstance(vi["amount_note"], str):
            raise ValueError(f"{where}.amount_note must be a string")
        if not _is_confidence(vi["confidence"]):
            raise ValueError(
                f"{where}.confidence must be a number between 0 and 1"
            )

    for i, d in enumerate(result["diffs"]):
        where = f"diffs[{i}]"
        _require_keys(d, _DIFF_KEYS, where)
        if d["type"] not in DIFF_TYPES:
            raise ValueError(
                f"{where}.type must be one of {DIFF_TYPES}, got {d['type']!r}"
            )
        for key in ("item", "detail"):
            if not isinstance(d[key], str):
                raise ValueError(f"{where}.{key} must be a string")
        for key in ("kcal_delta_min", "kcal_delta_max"):
            if not _is_int(d[key]):
                raise ValueError(f"{where}.{key} must be an integer")
        if d["kcal_delta_min"] > d["kcal_delta_max"]:
            raise ValueError(
                f"{where}.kcal_delta_min must not exceed kcal_delta_max, "
                f"got {d['kcal_delta_min']} > {d['kcal_delta_max']}"
            )
        if not _is_confidence(d["confidence"]):
            raise ValueError(
                f"{where}.confidence must be a number between 0 and 1"
            )


def estimate_delta(result: dict) -> dict:
    """`needs_check` を除く差異の推定 kcal 差分を、最小と最大の合計で返す。"""
    diffs = [d for d in result["diffs"] if d["type"] != "needs_check"]
    return {
        "min": sum(d["kcal_delta_min"] for d in diffs),
        "max": sum(d["kcal_delta_max"] for d in diffs),
    }


def item_lines(items: list[dict]) -> list[str]:
    """record の品目を、プロンプトに載せる 1 品目 1 行の形にする。"""
    return [
        f"- {i['menu_name']} x{i['quantity']} ({i['energy_kcal']}kcal)"
        for i in items
    ]


def build_messages(lines: list[str], photos: list[bytes]) -> list[dict]:
    """品目の一覧と写真から、chat completions の messages を組み立てる。"""
    text = PROMPT_HEAD + "\n".join(lines)
    content: list[dict] = [{"type": "text", "text": text}]
    for photo in photos:
        url = "data:image/jpeg;base64," + base64.b64encode(photo).decode()
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": content}]


def _input_hash(
    lines: list[str],
    photos: list[bytes],
    primary: str,
    verify: str,
    threshold_kcal: int,
) -> str:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "items": lines,
        "photos": [hashlib.sha256(p).hexdigest() for p in photos],
        "primary": primary,
        "verify": verify,
        "threshold": threshold_kcal,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _ask(azure: AzureClient, deployment: str, messages: list[dict]) -> dict:
    result = azure.chat_json(deployment, messages, SCHEMA)
    validate_result(result)
    return result


def _audit_meal(
    azure: AzureClient,
    messages: list[dict],
    input_hash: str,
    label: str,
    *,
    primary: str,
    verify: str,
    threshold_kcal: int,
) -> dict:
    try:
        primary_result = _ask(azure, primary, messages)
    except (AzureError, ValueError) as e:
        logger.exception("Audit failed for %s", label)
        return {"status": "error", "input_hash": input_hash, "error": str(e)}

    meal = {
        "status": "ok",
        "input_hash": input_hash,
        "verified": False,
        "result": primary_result,
        "verification_error": None,
    }
    # needs_check は飲み物などで常に出るため、裏取りの判定から除く。
    # しきい値の既定値と根拠は README の「裏取りのしきい値」にある。
    if estimate_delta(primary_result)["max"] >= threshold_kcal:
        try:
            verified_result = _ask(azure, verify, messages)
        except (AzureError, ValueError) as e:
            logger.exception("Verification failed for %s", label)
            meal["verification_error"] = str(e)
        else:
            meal.update(
                verified=True, result=verified_result, primary=primary_result
            )
    meal["estimated_kcal_delta"] = estimate_delta(meal["result"])
    return meal


def audit_day(
    date_str: str,
    data_dir: Path,
    azure: AzureClient,
    *,
    primary: str,
    verify: str,
    threshold_kcal: int,
    now: dt.datetime,
) -> dict | None:
    """1 日分の保存済みデータを照合して `audit.json` に保存し、保存した内容を返す。

    写真がある食事区分だけを対象にする。`record.json` がなければ何もせず None を返す。
    入力 (品目、写真、デプロイ名、しきい値、プロンプトの版) が前回と同じで、前回が
    成功 (裏取りの失敗も含まない) していれば、API を呼ばずに前回の結果を引き継ぐ。
    区分ごとの失敗は `status: "error"` として記録し、残りの区分の照合を続ける。
    """
    day_dir = data_dir / date_str
    record = read_json(day_dir / "record.json")
    if record is None:
        logger.warning("No record.json for %s, skipping audit", date_str)
        return None

    previous = read_json(day_dir / "audit.json") or {}
    previous_meals = previous.get("meals", {})

    meals: dict[str, dict] = {}
    for meal_type in MEAL_TYPES:
        meal = record["meals"].get(meal_type)
        if not meal or not meal["photos"]:
            continue
        label = f"{date_str} {meal_type}"
        photos = [
            path.read_bytes()
            for path in (day_dir / "photos" / name for name in meal["photos"])
            if path.exists()
        ]
        if not photos:
            logger.warning("No photo files for %s, skipping", label)
            continue

        lines = item_lines(meal["items"])
        input_hash = _input_hash(
            lines, photos, primary, verify, threshold_kcal
        )
        prev = previous_meals.get(meal_type)
        if (
            prev
            and prev["status"] == "ok"
            and prev["input_hash"] == input_hash
            and prev["verification_error"] is None
        ):
            logger.info("Unchanged input for %s, reusing audit", label)
            meals[meal_type] = prev
            continue

        meals[meal_type] = _audit_meal(
            azure,
            build_messages(lines, photos),
            input_hash,
            label,
            primary=primary,
            verify=verify,
            threshold_kcal=threshold_kcal,
        )
        logger.info(
            "Audited %s: status=%s verified=%s estimated_kcal_delta=%s",
            label,
            meals[meal_type]["status"],
            meals[meal_type].get("verified"),
            meals[meal_type].get("estimated_kcal_delta"),
        )

    audit = {
        "date": date_str,
        "audited_at": now.isoformat(),
        "models": {"primary": primary, "verify": verify},
        "verify_threshold_kcal": threshold_kcal,
        "meals": meals,
    }
    write_json_atomic(day_dir / "audit.json", audit)
    return audit

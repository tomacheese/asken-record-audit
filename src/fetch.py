"""あすけんの 1 日分の食事記録と写真を取得し、`data/<日付>/` に保存する。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from asken_client import MEAL_LABEL, MEAL_TYPES, AskenClient, MealRecord
from storage import write_json_atomic

JST = ZoneInfo("Asia/Tokyo")


def fetch_day(client: AskenClient, date_str: str, data_dir: Path) -> dict:
    """1 日分の食事記録と写真を取得して保存し、保存した record を返す。

    全区分の記録と写真をメモリ上に取得してから保存する。
    途中で失敗したら例外を送出し、既存の `record.json` と写真は変更しない。
    呼び出し側が事前に `client.login()` を済ませていること。
    """
    meals: dict[str, MealRecord] = {}
    photo_data: dict[str, list[bytes]] = {}
    for meal_type in MEAL_TYPES:
        meal = client.fetch_meal(meal_type, date_str)
        meals[meal_type] = meal
        photo_data[meal_type] = [
            client.download_photo(url) for url in meal.photo_urls
        ]

    record = _build_record(date_str, meals, dt.datetime.now(JST))

    day_dir = data_dir / date_str
    photos_dir = day_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    for meal_type, blobs in photo_data.items():
        for name, blob in zip(record["meals"][meal_type]["photos"], blobs):
            _write_bytes_atomic(photos_dir / name, blob)
    write_json_atomic(day_dir / "record.json", record)

    for meal_type in MEAL_TYPES:
        keep = set(record["meals"][meal_type]["photos"])
        for stale in photos_dir.glob(f"{meal_type}_*.jpg"):
            if stale.name not in keep:
                stale.unlink()
    return record


def _build_record(
    date_str: str, meals: dict[str, MealRecord], now: dt.datetime
) -> dict:
    meal_records = {}
    for meal_type in MEAL_TYPES:
        meal = meals[meal_type]
        meal_records[meal_type] = {
            "label": MEAL_LABEL[meal_type],
            "items": meal.items,
            "total_energy_kcal": meal.total_energy_kcal,
            "photos": [
                f"{meal_type}_{n}.jpg"
                for n in range(1, len(meal.photo_urls) + 1)
            ],
        }
    return {
        "date": date_str,
        "updated_at": now.isoformat(),
        "total_energy_kcal": sum(
            m["total_energy_kcal"] for m in meal_records.values()
        ),
        "meals": meal_records,
    }


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)

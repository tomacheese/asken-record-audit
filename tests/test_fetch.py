"""取得 (fetch_day) のテスト。"""
import json

import pytest

import fetch
from asken_client import MEAL_TYPES, MealRecord


def item(name, kcal):
    return {
        "item_hash": f"hash-{name}",
        "menu_id": "1",
        "menu_name": name,
        "quantity": "1",
        "energy_kcal": kcal,
    }


class FakeClient:
    def __init__(self, meals, photos=None, fail_meal=None, fail_photo=None):
        self.meals = meals
        self.photos = photos or {}
        self.fail_meal = fail_meal
        self.fail_photo = fail_photo

    def fetch_meal(self, meal_type, date):
        if meal_type == self.fail_meal:
            raise RuntimeError(f"fetch failed: {meal_type}")
        return self.meals.get(
            meal_type, MealRecord(meal_type, fetch.MEAL_LABEL[meal_type])
        )

    def download_photo(self, url_path):
        if url_path == self.fail_photo:
            raise RuntimeError(f"download failed: {url_path}")
        return self.photos[url_path]


def lunch_meal():
    return MealRecord(
        "lunch",
        "昼食",
        items=[item("ご飯", "156"), item("味噌汁", "40")],
        photo_urls=["/p/a/fit_380x380", "/p/b/fit_380x380"],
    )


def test_fetch_day_saves_record_and_photos(tmp_path):
    client = FakeClient(
        {"lunch": lunch_meal()},
        photos={"/p/a/fit_380x380": b"AAA", "/p/b/fit_380x380": b"BBB"},
    )

    record = fetch.fetch_day(client, "2026-10-23", tmp_path)

    day_dir = tmp_path / "2026-10-23"
    saved = json.loads((day_dir / "record.json").read_text(encoding="utf-8"))
    assert saved == record
    assert saved["date"] == "2026-10-23"
    assert saved["total_energy_kcal"] == 196
    assert list(saved["meals"]) == MEAL_TYPES
    assert saved["meals"]["lunch"] == {
        "label": "昼食",
        "items": [item("ご飯", "156"), item("味噌汁", "40")],
        "total_energy_kcal": 196,
        "photos": ["lunch_1.jpg", "lunch_2.jpg"],
    }
    assert (day_dir / "photos" / "lunch_1.jpg").read_bytes() == b"AAA"
    assert (day_dir / "photos" / "lunch_2.jpg").read_bytes() == b"BBB"
    assert "summary" not in saved
    assert saved["updated_at"].endswith("+09:00")


def test_fetch_day_includes_empty_meals(tmp_path):
    record = fetch.fetch_day(FakeClient({}), "2026-10-23", tmp_path)

    assert record["meals"]["breakfast"] == {
        "label": "朝食",
        "items": [],
        "total_energy_kcal": 0,
        "photos": [],
    }
    assert record["total_energy_kcal"] == 0


def seed_existing(tmp_path):
    day_dir = tmp_path / "2026-10-23"
    (day_dir / "photos").mkdir(parents=True)
    (day_dir / "record.json").write_text('{"old": true}', encoding="utf-8")
    (day_dir / "photos" / "lunch_1.jpg").write_bytes(b"OLD")
    return day_dir


def test_fetch_day_keeps_existing_files_when_a_meal_fetch_fails(tmp_path):
    day_dir = seed_existing(tmp_path)
    client = FakeClient(
        {"lunch": lunch_meal()},
        photos={"/p/a/fit_380x380": b"AAA", "/p/b/fit_380x380": b"BBB"},
        fail_meal="dinner",
    )

    with pytest.raises(RuntimeError, match="fetch failed: dinner"):
        fetch.fetch_day(client, "2026-10-23", tmp_path)

    record = day_dir / "record.json"
    assert record.read_text(encoding="utf-8") == '{"old": true}'
    assert (day_dir / "photos" / "lunch_1.jpg").read_bytes() == b"OLD"


def test_fetch_day_keeps_existing_files_when_a_photo_download_fails(tmp_path):
    day_dir = seed_existing(tmp_path)
    client = FakeClient(
        {"lunch": lunch_meal()},
        photos={"/p/a/fit_380x380": b"AAA"},
        fail_photo="/p/b/fit_380x380",
    )

    with pytest.raises(
        RuntimeError, match="download failed: /p/b/fit_380x380"
    ):
        fetch.fetch_day(client, "2026-10-23", tmp_path)

    record = day_dir / "record.json"
    assert record.read_text(encoding="utf-8") == '{"old": true}'
    assert (day_dir / "photos" / "lunch_1.jpg").read_bytes() == b"OLD"
    assert not (day_dir / "photos" / "lunch_2.jpg").exists()


def test_fetch_day_removes_stale_photos_of_the_same_meal(tmp_path):
    day_dir = seed_existing(tmp_path)
    (day_dir / "photos" / "lunch_2.jpg").write_bytes(b"OLD2")
    (day_dir / "photos" / "dinner_1.jpg").write_bytes(b"DINNER")
    meal = MealRecord(
        "lunch",
        "昼食",
        items=[item("ご飯", "156")],
        photo_urls=["/p/a/fit_380x380"],
    )

    fetch.fetch_day(
        FakeClient({"lunch": meal}, photos={"/p/a/fit_380x380": b"NEW"}),
        "2026-10-23",
        tmp_path,
    )

    photos = sorted(p.name for p in (day_dir / "photos").iterdir())
    assert photos == ["lunch_1.jpg"]
    assert (day_dir / "photos" / "lunch_1.jpg").read_bytes() == b"NEW"

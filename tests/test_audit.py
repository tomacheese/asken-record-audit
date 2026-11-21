"""照合 (audit_day) のテスト。Azure の応答はスタブで差し替える。"""
import base64
import datetime as dt
import json
import re
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import audit
from azure_client import AzureError
from storage import write_json_atomic

DATE = "2026-10-23"
NOW = dt.datetime(2026, 10, 24, 8, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
KW = {"primary": "luna", "verify": "terra", "threshold_kcal": 150, "now": NOW}


def diff(type_="missing_in_input", kmin=0, kmax=0, item="x", confidence=0.9):
    return {
        "type": type_,
        "item": item,
        "detail": "d",
        "kcal_delta_min": kmin,
        "kcal_delta_max": kmax,
        "confidence": confidence,
    }


def result(*diffs, summary="s"):
    return {
        "visible_items": [
            {"name": "ご飯", "count": 1, "amount_note": "1 杯", "confidence": 0.9}
        ],
        "diffs": list(diffs),
        "summary": summary,
    }


class FakeAzure:
    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def chat_json(self, deployment, messages, schema):
        self.calls.append((deployment, messages, schema))
        outcome = self.script[deployment].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def deployments(self):
        return [c[0] for c in self.calls]


def item(name, kcal):
    return {
        "item_hash": f"h-{name}",
        "menu_id": "1",
        "menu_name": name,
        "quantity": "1",
        "energy_kcal": kcal,
    }


@pytest.fixture
def day(tmp_path):
    """lunch と dinner に写真があり、breakfast と sweets には写真がない 1 日分のデータ。"""
    day_dir = tmp_path / DATE
    (day_dir / "photos").mkdir(parents=True)
    meals = {
        "breakfast": {
            "label": "朝食",
            "items": [item("パン", "200")],
            "photos": [],
        },
        "lunch": {
            "label": "昼食",
            "items": [item("ご飯", "156"), item("味噌汁", "40")],
            "photos": ["lunch_1.jpg", "lunch_2.jpg"],
        },
        "dinner": {
            "label": "夕食",
            "items": [item("カレー", "700")],
            "photos": ["dinner_1.jpg"],
        },
        "sweets": {"label": "間食", "items": [], "photos": []},
    }
    record = {
        "date": DATE,
        "updated_at": "x",
        "total_energy_kcal": 1,
        "meals": meals,
    }
    write_json_atomic(day_dir / "record.json", record)
    (day_dir / "photos" / "lunch_1.jpg").write_bytes(b"L1")
    (day_dir / "photos" / "lunch_2.jpg").write_bytes(b"L2")
    (day_dir / "photos" / "dinner_1.jpg").write_bytes(b"D1")
    return tmp_path


def read_audit(data_dir):
    path = data_dir / DATE / "audit.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_audits_only_meals_with_photos(day):
    azure = FakeAzure({"luna": [result(), result()]})

    written = audit.audit_day(DATE, day, azure, **KW)

    saved = read_audit(day)
    assert saved == written
    assert list(saved["meals"]) == ["lunch", "dinner"]
    assert saved["date"] == DATE
    assert saved["audited_at"] == "2026-10-24T08:00:00+09:00"
    assert saved["models"] == {"primary": "luna", "verify": "terra"}
    assert saved["verify_threshold_kcal"] == 150
    assert azure.deployments() == ["luna", "luna"]


def test_meal_without_verification_has_primary_result_only(day):
    azure = FakeAzure({"luna": [result(diff(kmin=10, kmax=50)), result()]})

    meal = audit.audit_day(DATE, day, azure, **KW)["meals"]["lunch"]

    assert meal["status"] == "ok"
    assert meal["verified"] is False
    assert meal["verification_error"] is None
    assert meal["result"] == result(diff(kmin=10, kmax=50))
    assert "primary" not in meal
    assert meal["estimated_kcal_delta"] == {"min": 10, "max": 50}
    assert len(meal["input_hash"]) == 64


def test_needs_check_is_excluded_from_verification_and_estimate(day):
    big_drink = diff("needs_check", 0, 500, item="飲み物")
    azure = FakeAzure(
        {"luna": [result(big_drink, diff(kmin=5, kmax=20)), result()]}
    )

    meal = audit.audit_day(DATE, day, azure, **KW)["meals"]["lunch"]

    assert azure.deployments() == ["luna", "luna"]
    assert meal["estimated_kcal_delta"] == {"min": 5, "max": 20}


def test_verifies_at_threshold_and_adopts_the_verified_result(day):
    primary = result(diff(kmin=100, kmax=150, item="ご飯"))
    verified = result(diff(kmin=120, kmax=180, item="ご飯"), summary="verified")
    azure = FakeAzure({"luna": [primary, result()], "terra": [verified]})

    meal = audit.audit_day(DATE, day, azure, **KW)["meals"]["lunch"]

    assert azure.deployments() == ["luna", "terra", "luna"]
    assert meal["verified"] is True
    assert meal["verification_error"] is None
    assert meal["result"] == verified
    assert meal["primary"] == primary
    assert meal["estimated_kcal_delta"] == {"min": 120, "max": 180}


def test_verification_gets_the_same_input_and_not_the_primary_result(day):
    primary = result(diff(kmin=0, kmax=300, item="PRIMARY-ONLY-MARKER"))
    azure = FakeAzure({"luna": [primary, result()], "terra": [result()]})

    audit.audit_day(DATE, day, azure, **KW)

    luna_call, terra_call = azure.calls[0], azure.calls[1]
    assert terra_call[1] == luna_call[1]
    assert terra_call[2] == audit.SCHEMA
    sent = json.dumps(terra_call[1], ensure_ascii=False)
    assert "PRIMARY-ONLY-MARKER" not in sent


def test_threshold_is_configurable(day):
    azure = FakeAzure(
        {"luna": [result(diff(kmax=50)), result()], "terra": [result()]}
    )

    audit.audit_day(DATE, day, azure, **{**KW, "threshold_kcal": 50})

    assert azure.deployments() == ["luna", "terra", "luna"]


def test_threshold_boundary_below_does_not_verify(day):
    azure = FakeAzure({"luna": [result(diff(kmax=149)), result()]})

    audit.audit_day(DATE, day, azure, **KW)

    assert azure.deployments() == ["luna", "luna"]


def test_failed_verification_keeps_primary_result_and_records_error(day):
    primary = result(diff(kmax=300))
    azure = FakeAzure(
        {"luna": [primary, result()], "terra": [AzureError("HTTP 500: boom")]}
    )

    meal = audit.audit_day(DATE, day, azure, **KW)["meals"]["lunch"]

    assert meal["status"] == "ok"
    assert meal["verified"] is False
    assert meal["verification_error"] == "HTTP 500: boom"
    assert meal["result"] == primary
    assert "primary" not in meal
    assert meal["estimated_kcal_delta"] == {"min": 0, "max": 300}


def test_primary_failure_is_recorded_and_other_meals_are_processed(day):
    azure = FakeAzure({"luna": [AzureError("gave up"), result(diff(kmax=10))]})

    written = audit.audit_day(DATE, day, azure, **KW)

    lunch = written["meals"]["lunch"]
    assert lunch["status"] == "error"
    assert lunch["error"] == "gave up"
    assert len(lunch["input_hash"]) == 64
    assert written["meals"]["dinner"]["status"] == "ok"
    assert read_audit(day) == written


def test_schema_violating_response_is_recorded_as_error(day):
    bad = result()
    bad["diffs"] = [diff("unknown_type")]
    azure = FakeAzure({"luna": [bad, result()]})

    lunch = audit.audit_day(DATE, day, azure, **KW)["meals"]["lunch"]

    assert lunch["status"] == "error"
    assert lunch["error"].startswith("diffs[0].type must be one of")


def test_record_and_photos_are_not_modified(day):
    record_before = (day / DATE / "record.json").read_bytes()
    photos_before = {
        p.name: p.read_bytes() for p in (day / DATE / "photos").iterdir()
    }
    azure = FakeAzure(
        {"luna": [result(diff(kmax=300)), result()], "terra": [result()]}
    )

    audit.audit_day(DATE, day, azure, **KW)

    assert (day / DATE / "record.json").read_bytes() == record_before
    assert {
        p.name: p.read_bytes() for p in (day / DATE / "photos").iterdir()
    } == photos_before


def test_returns_none_without_record(tmp_path):
    azure = FakeAzure({})

    assert audit.audit_day(DATE, tmp_path, azure, **KW) is None
    assert not (tmp_path / DATE / "audit.json").exists()
    assert azure.calls == []


def test_meal_whose_photo_files_are_missing_is_skipped(day):
    (day / DATE / "photos" / "dinner_1.jpg").unlink()
    azure = FakeAzure({"luna": [result()]})

    written = audit.audit_day(DATE, day, azure, **KW)

    assert list(written["meals"]) == ["lunch"]


def test_only_existing_photos_are_sent(day):
    (day / DATE / "photos" / "lunch_2.jpg").unlink()
    azure = FakeAzure({"luna": [result(), result()]})

    audit.audit_day(DATE, day, azure, **KW)

    content = azure.calls[0][1][0]["content"]
    assert [c["type"] for c in content] == ["text", "image_url"]


def test_unchanged_meals_are_not_audited_again(day):
    first = FakeAzure({"luna": [result(), result()]})
    audit.audit_day(DATE, day, first, **KW)
    second = FakeAzure({})

    written = audit.audit_day(DATE, day, second, **KW)

    assert second.calls == []
    assert written["meals"]["lunch"]["status"] == "ok"


@pytest.mark.parametrize(
    "change",
    [
        {"primary": "luna2"},
        {"verify": "terra2"},
        {"threshold_kcal": 151},
    ],
)
def test_changed_models_or_threshold_trigger_audit_again(day, change):
    audit.audit_day(DATE, day, FakeAzure({"luna": [result(), result()]}), **KW)
    new_kw = {**KW, **change}
    second = FakeAzure({new_kw["primary"]: [result(), result()]})

    audit.audit_day(DATE, day, second, **new_kw)

    assert len(second.calls) == 2


def test_changed_photo_triggers_audit_again(day):
    audit.audit_day(DATE, day, FakeAzure({"luna": [result(), result()]}), **KW)
    (day / DATE / "photos" / "dinner_1.jpg").write_bytes(b"CHANGED")
    second = FakeAzure({"luna": [result()]})

    audit.audit_day(DATE, day, second, **KW)

    assert second.deployments() == ["luna"]


def test_changed_items_trigger_audit_again(day):
    audit.audit_day(DATE, day, FakeAzure({"luna": [result(), result()]}), **KW)
    path = day / DATE / "record.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["meals"]["dinner"]["items"] = [item("カレー", "800")]
    write_json_atomic(path, record)
    second = FakeAzure({"luna": [result()]})

    audit.audit_day(DATE, day, second, **KW)

    assert second.deployments() == ["luna"]


def test_error_and_failed_verification_meals_are_retried(day):
    first = FakeAzure(
        {
            "luna": [AzureError("transient"), result(diff(kmax=300))],
            "terra": [AzureError("transient too")],
        }
    )
    audit.audit_day(DATE, day, first, **KW)
    second = FakeAzure(
        {"luna": [result(), result(diff(kmax=300))], "terra": [result()]}
    )

    written = audit.audit_day(DATE, day, second, **KW)

    assert second.deployments() == ["luna", "luna", "terra"]
    assert written["meals"]["lunch"]["status"] == "ok"
    assert written["meals"]["dinner"]["verified"] is True


def test_audit_json_is_written_atomically(day):
    azure = FakeAzure({"luna": [result(), result()]})

    with mock.patch.object(audit, "write_json_atomic") as write:
        audit.audit_day(DATE, day, azure, **KW)

    write.assert_called_once()
    assert write.call_args.args[0] == day / DATE / "audit.json"


def test_build_messages_contains_prompt_items_and_photos_in_order():
    lines = ["- ご飯 x1 (156kcal)", "- 味噌汁 x1 (40kcal)"]

    messages = audit.build_messages(lines, [b"AB", b"CD"])

    def image(data):
        url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
        return {"type": "image_url", "image_url": {"url": url}}

    assert messages == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": audit.PROMPT_HEAD + "\n".join(lines),
                },
                image(b"AB"),
                image(b"CD"),
            ],
        }
    ]


def test_estimate_delta_sums_non_needs_check_diffs():
    r = result(
        diff(kmin=10, kmax=20),
        diff("count_mismatch", -5, 30),
        diff("needs_check", 0, 999),
    )

    assert audit.estimate_delta(r) == {"min": 5, "max": 50}


def test_estimate_delta_of_no_diffs_is_zero():
    assert audit.estimate_delta(result()) == {"min": 0, "max": 0}


def test_validate_result_accepts_valid_result():
    audit.validate_result(result(diff(kmin=-10, kmax=10)))


@pytest.mark.parametrize(
    ("kmin", "kmax"), [(-300, -20), (-50, 50), (100, 300), (80, 80)]
)
def test_validate_result_accepts_ordered_delta_range(kmin, kmax):
    audit.validate_result(result(diff(kmin=kmin, kmax=kmax)))


def test_inverted_delta_range_is_recorded_as_error_without_verification(day):
    azure = FakeAzure(
        {"luna": [result(diff(kmin=300, kmax=20)), result()]}
    )

    lunch = audit.audit_day(DATE, day, azure, **KW)["meals"]["lunch"]

    assert lunch["status"] == "error"
    assert lunch["error"] == (
        "diffs[0].kcal_delta_min must not exceed kcal_delta_max, "
        "got 300 > 20"
    )
    assert azure.deployments() == ["luna", "luna"]


def test_validate_result_accepts_null_count():
    r = result()
    r["visible_items"][0]["count"] = None

    audit.validate_result(r)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.pop("summary"), "result must have keys"),
        (lambda r: r.update(summary=1), "summary must be a string"),
        (lambda r: r.update(diffs="x"), "diffs must be a list"),
        (
            lambda r: r["visible_items"][0].update(count="1"),
            "visible_items[0].count must be an integer or null",
        ),
        (
            lambda r: r["visible_items"][0].update(confidence=1.5),
            "visible_items[0].confidence must be a number between 0 and 1",
        ),
        (
            lambda r: r["diffs"][0].update(type="other"),
            "diffs[0].type must be one of",
        ),
        (
            lambda r: r["diffs"][0].update(kcal_delta_min="1"),
            "diffs[0].kcal_delta_min must be an integer",
        ),
        (
            lambda r: r["diffs"][0].update(kcal_delta_max=True),
            "diffs[0].kcal_delta_max must be an integer",
        ),
        (
            lambda r: r["diffs"][0].update(
                kcal_delta_min=300, kcal_delta_max=20
            ),
            "diffs[0].kcal_delta_min must not exceed kcal_delta_max, "
            "got 300 > 20",
        ),
        (
            lambda r: r["diffs"][0].update(
                kcal_delta_min=-20, kcal_delta_max=-300
            ),
            "diffs[0].kcal_delta_min must not exceed kcal_delta_max, "
            "got -20 > -300",
        ),
        (
            lambda r: r["diffs"][0].update(confidence=-0.1),
            "diffs[0].confidence must be a number between 0 and 1",
        ),
        (lambda r: r["diffs"][0].pop("item"), "diffs[0] must have keys"),
    ],
)
def test_validate_result_rejects_invalid_result(mutate, message):
    r = result(diff())
    mutate(r)

    with pytest.raises(ValueError, match=re.escape(message)):
        audit.validate_result(r)

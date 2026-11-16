"""設定・サイクル・常駐ループ (停止を含む) のテスト。"""
import datetime as dt
import logging
import signal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import main
from asken_client import LoginError

REQUIRED_ENV = {
    "ASKEN_USERNAME": "user@example.com",
    "ASKEN_PASSWORD": "pw-secret-value",
    "AZURE_OPENAI_ENDPOINT": "https://example.test/openai/v1",
    "AZURE_OPENAI_API_KEY": "key-secret-value",
    "AZURE_OPENAI_DEPLOYMENT": "luna",
    "AZURE_OPENAI_VERIFY_DEPLOYMENT": "terra",
    "VERIFY_THRESHOLD_KCAL": "150",
    "ASKEN_TRACK_DAYS": "3",
    "INTERVAL_SECONDS": "86400",
    "DATA_DIR": "/data",
}
TODAY = dt.datetime(2026, 10, 24, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.fixture(autouse=True)
def _reset_shutdown():
    main._shutdown.clear()
    yield
    main._shutdown.clear()


@pytest.fixture
def no_signal_handlers():
    with mock.patch.object(main.signal, "signal") as patched:
        yield patched


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RUN_ONCE", raising=False)
    monkeypatch.delenv("TARGET_DATE", raising=False)
    return monkeypatch


def make_config(**overrides):
    values = dict(
        asken_username="user@example.com",
        asken_password="pw-secret-value",
        azure_endpoint="https://example.test/openai/v1",
        azure_api_key="key-secret-value",
        primary_deployment="luna",
        verify_deployment="terra",
        verify_threshold_kcal=150,
        track_days=3,
        interval_seconds=86400,
        data_dir=Path("/data"),
        run_once=False,
        target_date=None,
    )
    values.update(overrides)
    return main.Config(**values)


# --- Config ---


def test_config_reads_all_settings(env):
    assert main.Config.from_env() == make_config()


def test_config_repr_hides_secrets():
    text = repr(make_config())

    assert "pw-secret-value" not in text
    assert "key-secret-value" not in text
    assert "user@example.com" in text


@pytest.mark.parametrize("name", sorted(REQUIRED_ENV))
def test_config_requires_variable(env, name):
    env.delenv(name)

    with pytest.raises(KeyError, match=name):
        main.Config.from_env()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("", False)],
)
def test_config_parses_run_once(env, value, expected):
    env.setenv("RUN_ONCE", value)

    assert main.Config.from_env().run_once is expected


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "ASKEN_TRACK_DAYS",
            "0",
            "ASKEN_TRACK_DAYS must be an integer >= 1, got '0'",
        ),
        (
            "ASKEN_TRACK_DAYS",
            "x",
            "ASKEN_TRACK_DAYS must be an integer >= 1, got 'x'",
        ),
        (
            "VERIFY_THRESHOLD_KCAL",
            "-1",
            "VERIFY_THRESHOLD_KCAL must be an integer >= 0, got '-1'",
        ),
        (
            "VERIFY_THRESHOLD_KCAL",
            "1.5",
            "VERIFY_THRESHOLD_KCAL must be an integer >= 0, got '1.5'",
        ),
        (
            "TARGET_DATE",
            "2026-13-01",
            "TARGET_DATE must be YYYY-MM-DD, got '2026-13-01'",
        ),
        (
            "TARGET_DATE",
            "20261023",
            "TARGET_DATE must be YYYY-MM-DD, got '20261023'",
        ),
        (
            "TARGET_DATE",
            "abc",
            "TARGET_DATE must be YYYY-MM-DD, got 'abc'",
        ),
    ],
)
def test_config_rejects_invalid_values(env, name, value, message):
    env.setenv(name, value)

    with pytest.raises(ValueError) as excinfo:
        main.Config.from_env()

    assert str(excinfo.value) == message


def test_config_accepts_zero_threshold(env):
    env.setenv("VERIFY_THRESHOLD_KCAL", "0")

    assert main.Config.from_env().verify_threshold_kcal == 0


def test_config_parses_target_date(env):
    env.setenv("TARGET_DATE", "2026-10-23")

    assert main.Config.from_env().target_date == dt.date(2026, 10, 23)


def test_config_treats_empty_target_date_as_unset(env):
    env.setenv("TARGET_DATE", "")

    assert main.Config.from_env().target_date is None


# --- target_dates ---


def test_target_dates_are_the_last_days_oldest_first():
    config = make_config(track_days=3)

    assert main.target_dates(config, dt.date(2026, 10, 24)) == [
        dt.date(2026, 10, 22),
        dt.date(2026, 10, 23),
        dt.date(2026, 10, 24),
    ]


def test_target_date_overrides_track_days():
    config = make_config(target_date=dt.date(2026, 10, 1))

    assert main.target_dates(config, dt.date(2026, 10, 24)) == [
        dt.date(2026, 10, 1)
    ]


# --- run_cycle ---


@pytest.fixture
def cycle():
    with (
        mock.patch.object(main, "_now", return_value=TODAY),
        mock.patch.object(main, "fetch_day") as fetch_day,
        mock.patch.object(main, "audit_day") as audit_day,
    ):
        yield fetch_day, audit_day


def test_run_cycle_fetches_then_audits_each_day_oldest_first(cycle):
    fetch_day, audit_day = cycle
    client, azure = mock.Mock(), mock.Mock()
    calls = mock.Mock()
    calls.attach_mock(client.login, "login")
    calls.attach_mock(fetch_day, "fetch")
    calls.attach_mock(audit_day, "audit")

    main.run_cycle(make_config(), client, azure)

    data_dir = Path("/data")
    audit_kwargs = dict(
        primary="luna", verify="terra", threshold_kcal=150, now=TODAY
    )
    assert calls.mock_calls == [
        mock.call.login(),
        mock.call.fetch(client, "2026-10-22", data_dir),
        mock.call.audit("2026-10-22", data_dir, azure, **audit_kwargs),
        mock.call.fetch(client, "2026-10-23", data_dir),
        mock.call.audit("2026-10-23", data_dir, azure, **audit_kwargs),
        mock.call.fetch(client, "2026-10-24", data_dir),
        mock.call.audit("2026-10-24", data_dir, azure, **audit_kwargs),
    ]


def test_run_cycle_continues_with_next_day_when_fetch_fails(cycle, caplog):
    fetch_day, audit_day = cycle
    fetch_day.side_effect = [RuntimeError("boom"), None, None]

    with caplog.at_level(logging.ERROR, logger="asken-record-audit"):
        main.run_cycle(make_config(), mock.Mock(), mock.Mock())

    assert fetch_day.call_count == 3
    assert [c.args[0] for c in audit_day.call_args_list] == [
        "2026-10-22",
        "2026-10-23",
        "2026-10-24",
    ]
    assert "Fetch failed for 2026-10-22" in caplog.text
    assert "RuntimeError: boom" in caplog.text


def test_run_cycle_continues_with_next_day_when_audit_fails(cycle, caplog):
    fetch_day, audit_day = cycle
    audit_day.side_effect = [RuntimeError("audit boom"), None, None]

    with caplog.at_level(logging.ERROR, logger="asken-record-audit"):
        main.run_cycle(make_config(), mock.Mock(), mock.Mock())

    assert audit_day.call_count == 3
    assert "Audit failed for 2026-10-22" in caplog.text


def test_run_cycle_stops_when_login_fails(cycle, caplog):
    fetch_day, audit_day = cycle
    client = mock.Mock()
    client.login.side_effect = LoginError(
        "Login failed (no logout link found in response)"
    )

    with caplog.at_level(logging.ERROR, logger="asken-record-audit"):
        main.run_cycle(make_config(), client, mock.Mock())

    fetch_day.assert_not_called()
    audit_day.assert_not_called()
    assert "Login to asken.jp failed" in caplog.text


def test_run_cycle_processes_only_target_date(cycle):
    fetch_day, audit_day = cycle

    main.run_cycle(
        make_config(target_date=dt.date(2026, 10, 1)), mock.Mock(), mock.Mock()
    )

    assert [c.args[1] for c in fetch_day.call_args_list] == ["2026-10-01"]
    assert [c.args[0] for c in audit_day.call_args_list] == ["2026-10-01"]


def test_run_cycle_does_not_log_credentials(cycle, caplog):
    fetch_day, _ = cycle
    fetch_day.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.DEBUG, logger="asken-record-audit"):
        main.run_cycle(make_config(), mock.Mock(), mock.Mock())

    assert "pw-secret-value" not in caplog.text
    assert "key-secret-value" not in caplog.text


# --- main loop ---


@pytest.fixture
def clients():
    with mock.patch.object(main, "AskenClient") as asken, mock.patch.object(
        main, "AzureClient"
    ) as azure:
        yield asken, azure


def test_main_builds_clients_and_runs_one_cycle_when_run_once(
    env, no_signal_handlers, clients
):
    env.setenv("RUN_ONCE", "1")
    asken, azure = clients

    with mock.patch.object(main, "run_cycle") as run_cycle:
        main.main()

    asken.assert_called_once_with("user@example.com", "pw-secret-value")
    azure.assert_called_once_with(
        "https://example.test/openai/v1",
        "key-secret-value",
        sleep=main._shutdown.wait,
    )
    run_cycle.assert_called_once_with(
        make_config(run_once=True),
        asken.return_value.__enter__.return_value,
        azure.return_value.__enter__.return_value,
    )


def test_main_continues_after_cycle_exception(
    env, no_signal_handlers, clients, caplog
):
    env.setenv("RUN_ONCE", "1")
    failing = mock.patch.object(
        main, "run_cycle", side_effect=RuntimeError("boom")
    )

    with failing:
        with caplog.at_level(logging.ERROR, logger="asken-record-audit"):
            main.main()

    assert "Unexpected error during cycle" in caplog.text
    assert "RuntimeError: boom" in caplog.text


def test_main_runs_next_cycle_after_a_failed_one(
    env, no_signal_handlers, clients
):
    sleeps = []

    def run_cycle(config, client, azure):
        if len(sleeps) == 0:
            raise RuntimeError("boom")
        main._request_shutdown(signal.SIGTERM, None)

    def wait(seconds):
        sleeps.append(seconds)
        return main._shutdown.is_set()

    with (
        mock.patch.object(
            main, "run_cycle", side_effect=run_cycle
        ) as cycle_mock,
        mock.patch.object(main._shutdown, "wait", side_effect=wait),
    ):
        main.main()

    assert cycle_mock.call_count == 2
    assert sleeps == [86400, 86400]


def test_request_shutdown_sets_event():
    main._request_shutdown(signal.SIGTERM, None)

    assert main._shutdown.is_set()


def test_main_registers_handlers_for_sigterm_and_sigint(
    env, no_signal_handlers, clients
):
    env.setenv("RUN_ONCE", "1")

    with mock.patch.object(main, "run_cycle"):
        main.main()

    no_signal_handlers.assert_has_calls(
        [
            mock.call(signal.SIGTERM, main._request_shutdown),
            mock.call(signal.SIGINT, main._request_shutdown),
        ],
        any_order=True,
    )


def test_main_stops_after_current_cycle_on_shutdown(
    env, no_signal_handlers, clients
):
    def request_shutdown_during_cycle(config, client, azure):
        main._request_shutdown(signal.SIGTERM, None)

    with mock.patch.object(
        main, "run_cycle", side_effect=request_shutdown_during_cycle
    ) as run_cycle:
        main.main()

    run_cycle.assert_called_once()


def test_main_stops_when_wait_is_interrupted(env, no_signal_handlers, clients):
    with (
        mock.patch.object(main, "run_cycle") as run_cycle,
        mock.patch.object(
            main._shutdown, "wait", return_value=True
        ) as wait,
    ):
        main.main()

    run_cycle.assert_called_once()
    wait.assert_called_once_with(86400)


def test_main_with_run_once_and_target_date_processes_that_date_only(
    env, no_signal_handlers, clients
):
    env.setenv("RUN_ONCE", "1")
    env.setenv("TARGET_DATE", "2026-10-23")

    with mock.patch.object(main, "fetch_day") as fetch_day, mock.patch.object(
        main, "audit_day"
    ) as audit_day:
        main.main()

    assert [c.args[1] for c in fetch_day.call_args_list] == ["2026-10-23"]
    assert [c.args[0] for c in audit_day.call_args_list] == ["2026-10-23"]

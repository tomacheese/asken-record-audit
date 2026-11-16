"""常駐ループの骨組み (設定・サイクル・停止) のテスト。"""
import logging
import signal
from unittest import mock

import pytest

import main


@pytest.fixture(autouse=True)
def _reset_shutdown():
    main._shutdown.clear()
    yield
    main._shutdown.clear()


@pytest.fixture
def no_signal_handlers():
    with mock.patch.object(main.signal, "signal") as patched:
        yield patched


def test_run_cycle_logs_startup(caplog):
    with caplog.at_level(logging.INFO, logger="asken-record-audit"):
        main.run_cycle()

    assert "asken-record-audit started" in caplog.text


def test_config_reads_interval_seconds(monkeypatch):
    monkeypatch.setenv("INTERVAL_SECONDS", "86400")
    monkeypatch.delenv("RUN_ONCE", raising=False)

    assert main.Config.from_env() == main.Config(
        interval_seconds=86400, run_once=False
    )


def test_config_requires_interval_seconds(monkeypatch):
    monkeypatch.delenv("INTERVAL_SECONDS", raising=False)

    with pytest.raises(KeyError, match="INTERVAL_SECONDS"):
        main.Config.from_env()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("", False)],
)
def test_config_parses_run_once(monkeypatch, value, expected):
    monkeypatch.setenv("INTERVAL_SECONDS", "1")
    monkeypatch.setenv("RUN_ONCE", value)

    assert main.Config.from_env().run_once is expected


def test_main_runs_one_cycle_when_run_once(monkeypatch, no_signal_handlers):
    monkeypatch.setenv("INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("RUN_ONCE", "1")

    with mock.patch.object(main, "run_cycle") as run_cycle:
        main.main()

    run_cycle.assert_called_once_with()


def test_main_continues_after_cycle_exception(
    monkeypatch, no_signal_handlers, caplog
):
    monkeypatch.setenv("INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("RUN_ONCE", "1")

    with mock.patch.object(
        main, "run_cycle", side_effect=RuntimeError("boom")
    ):
        with caplog.at_level(logging.ERROR, logger="asken-record-audit"):
            main.main()

    assert "Unexpected error during cycle" in caplog.text
    assert "RuntimeError: boom" in caplog.text


def test_request_shutdown_sets_event():
    main._request_shutdown(signal.SIGTERM, None)

    assert main._shutdown.is_set()


def test_main_registers_handlers_for_sigterm_and_sigint(
    monkeypatch, no_signal_handlers
):
    monkeypatch.setenv("INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("RUN_ONCE", "1")

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
    monkeypatch, no_signal_handlers
):
    monkeypatch.setenv("INTERVAL_SECONDS", "3600")
    monkeypatch.delenv("RUN_ONCE", raising=False)

    def request_shutdown_during_cycle():
        main._request_shutdown(signal.SIGTERM, None)

    with mock.patch.object(
        main, "run_cycle", side_effect=request_shutdown_during_cycle
    ) as run_cycle:
        main.main()

    run_cycle.assert_called_once_with()

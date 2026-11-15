"""main.main() が起動ログを出して正常に戻ることのテスト。"""
import logging

import main


def test_main_logs_startup(caplog):
    with caplog.at_level(logging.INFO, logger="asken-record-audit"):
        main.main()

    assert "asken-record-audit started" in caplog.text

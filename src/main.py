"""あすけんの食事記録を写真と照合して監査する常駐プロセス。

スケジューリングは cron やタイマーではなくこのプロセス自身のループで行うため、
コンテナは起動し続けるだけでよい。
"""
from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import dataclass
from types import FrameType

logger = logging.getLogger("asken-record-audit")

_shutdown = threading.Event()


@dataclass(frozen=True)
class Config:
    """プロセスの環境変数から解決した実行時設定。"""

    interval_seconds: int
    run_once: bool

    @classmethod
    def from_env(cls) -> Config:
        """環境変数から Config を組み立てる。

        既定値は Dockerfile の ENV にあるため、コード側で二重に既定値を持たず、
        未設定なら KeyError を送出する。
        """
        return cls(
            interval_seconds=int(os.environ["INTERVAL_SECONDS"]),
            run_once=os.environ.get("RUN_ONCE", "").lower()
            in ("1", "true", "yes"),
        )


def run_cycle() -> None:
    """取得・照合・保存の 1 サイクルを実行する。"""
    logger.info("asken-record-audit started")


def _request_shutdown(signum: int, frame: FrameType | None) -> None:
    """現在のサイクルの終了後にメインループを止めるよう要求するシグナルハンドラ。"""
    logger.info(
        "Received signal %s, shutting down after current cycle", signum
    )
    _shutdown.set()


def main() -> None:
    """設定を読み込み、中断されるまでサイクルを繰り返す。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    config = Config.from_env()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    while not _shutdown.is_set():
        try:
            run_cycle()
        except Exception:
            # 1 サイクルの失敗で常駐プロセスを落としてはならない。
            logger.exception("Unexpected error during cycle")

        if config.run_once:
            break
        logger.info(
            "Sleeping %s seconds until next cycle", config.interval_seconds
        )
        if _shutdown.wait(config.interval_seconds):
            break


if __name__ == "__main__":
    main()

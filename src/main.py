"""あすけんの食事記録を写真と照合して監査する常駐プロセス。

スケジューリングは cron やタイマーではなくこのプロセス自身のループで行うため、
コンテナは起動し続けるだけでよい。
各サイクルでは、あすけんから直近の食事記録と写真を取得して保存し、保存済みのデータを
Azure OpenAI で照合して、結果を `data/<日付>/audit.json` に保存する。
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from zoneinfo import ZoneInfo

from asken_client import AskenClient, LoginError
from audit import audit_day
from azure_client import AzureClient
from fetch import fetch_day

JST = ZoneInfo("Asia/Tokyo")

logger = logging.getLogger("asken-record-audit")

_shutdown = threading.Event()


def _int_env(name: str, minimum: int) -> int:
    value = os.environ[name]
    try:
        number = int(value)
    except ValueError:
        number = minimum - 1
    if number < minimum:
        raise ValueError(
            f"{name} must be an integer >= {minimum}, got {value!r}"
        )
    return number


def _target_date_env() -> dt.date | None:
    value = os.environ.get("TARGET_DATE", "")
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"TARGET_DATE must be YYYY-MM-DD, got {value!r}"
        ) from None


@dataclass(frozen=True)
class Config:
    """プロセスの環境変数から解決した実行時設定。"""

    asken_username: str
    asken_password: str = field(repr=False)
    azure_endpoint: str
    azure_api_key: str = field(repr=False)
    primary_deployment: str
    verify_deployment: str
    verify_threshold_kcal: int
    track_days: int
    interval_seconds: int
    data_dir: Path
    run_once: bool
    target_date: dt.date | None

    @classmethod
    def from_env(cls) -> Config:
        """環境変数から Config を組み立てる。

        既定値は Dockerfile の ENV にあるため、コード側で二重に既定値を持たず、
        未設定なら KeyError を送出する。任意なのは `RUN_ONCE` と `TARGET_DATE` だけ。
        整数や日付が不正なら ValueError を送出する。
        """
        return cls(
            asken_username=os.environ["ASKEN_USERNAME"],
            asken_password=os.environ["ASKEN_PASSWORD"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            azure_api_key=os.environ["AZURE_OPENAI_API_KEY"],
            primary_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            verify_deployment=os.environ["AZURE_OPENAI_VERIFY_DEPLOYMENT"],
            verify_threshold_kcal=_int_env("VERIFY_THRESHOLD_KCAL", 0),
            track_days=_int_env("ASKEN_TRACK_DAYS", 1),
            interval_seconds=int(os.environ["INTERVAL_SECONDS"]),
            data_dir=Path(os.environ["DATA_DIR"]),
            run_once=os.environ.get("RUN_ONCE", "").lower()
            in ("1", "true", "yes"),
            target_date=_target_date_env(),
        )


def _now() -> dt.datetime:
    return dt.datetime.now(JST)


def target_dates(config: Config, today: dt.date) -> list[dt.date]:
    """対象日を古い順に返す。`target_date` があればその 1 日だけ。"""
    if config.target_date is not None:
        return [config.target_date]
    days_back = range(config.track_days - 1, -1, -1)
    return [today - dt.timedelta(days=i) for i in days_back]


def run_cycle(config: Config, client: AskenClient, azure: AzureClient) -> None:
    """取得・照合・保存の 1 サイクルを実行する。

    日ごとの失敗は他の日に影響させない。取得に失敗した日も、保存済みのデータがあれば
    照合する。
    """
    dates = [d.isoformat() for d in target_dates(config, _now().date())]
    logger.info("Cycle started: dates=%s", ", ".join(dates))
    try:
        client.login()
    except LoginError:
        logger.exception("Login to asken.jp failed")
        return

    for date_str in dates:
        try:
            fetch_day(client, date_str, config.data_dir)
        except Exception:
            logger.exception("Fetch failed for %s", date_str)
        try:
            audit_day(
                date_str,
                config.data_dir,
                azure,
                primary=config.primary_deployment,
                verify=config.verify_deployment,
                threshold_kcal=config.verify_threshold_kcal,
                now=_now(),
            )
        except Exception:
            logger.exception("Audit failed for %s", date_str)
    logger.info("Cycle finished")


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

    with (
        AskenClient(config.asken_username, config.asken_password) as client,
        AzureClient(
            config.azure_endpoint, config.azure_api_key, sleep=_shutdown.wait
        ) as azure,
    ):
        while not _shutdown.is_set():
            try:
                run_cycle(config, client, azure)
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

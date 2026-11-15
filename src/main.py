"""あすけんの食事記録を写真と照合して監査するバッチの起動点。"""
import logging

logger = logging.getLogger("asken-record-audit")


def main() -> None:
    """ログを設定して起動を記録する。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("asken-record-audit started")


if __name__ == "__main__":
    main()

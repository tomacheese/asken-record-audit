"""JSON ファイルの原子的な書き込みと読み込み。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any) -> None:
    """一時ファイルに書いてから置き換えることで、JSON を原子的に書き込む。

    親ディレクトリがなければ作る。置き換えに失敗しても既存のファイルは壊れない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def read_json(path: Path) -> Any | None:
    """JSON を読み込む。ファイルがない、または壊れている場合は None を返す。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

"""JSON の原子的な書き込みと読み込みのテスト。"""
import json
from pathlib import Path
from unittest import mock

import pytest

import storage


def test_write_json_atomic_creates_parent_and_file(tmp_path):
    path = tmp_path / "a" / "b" / "x.json"

    storage.write_json_atomic(path, {"k": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"k": 1}
    assert list(path.parent.iterdir()) == [path]


def test_write_json_atomic_overwrites(tmp_path):
    path = tmp_path / "x.json"
    storage.write_json_atomic(path, {"k": 1})

    storage.write_json_atomic(path, {"k": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"k": 2}


def test_write_json_atomic_keeps_existing_file_when_replace_fails(tmp_path):
    path = tmp_path / "x.json"
    storage.write_json_atomic(path, {"k": 1})

    with mock.patch.object(Path, "replace", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            storage.write_json_atomic(path, {"k": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"k": 1}


def test_write_json_atomic_keeps_japanese_unescaped(tmp_path):
    path = tmp_path / "x.json"

    storage.write_json_atomic(path, {"name": "ご飯"})

    assert "ご飯" in path.read_text(encoding="utf-8")


def test_read_json_returns_none_when_missing(tmp_path):
    assert storage.read_json(tmp_path / "missing.json") is None


def test_read_json_returns_none_when_corrupt(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{not json", encoding="utf-8")

    assert storage.read_json(path) is None


def test_read_json_returns_data(tmp_path):
    path = tmp_path / "x.json"
    storage.write_json_atomic(path, [1, 2])

    assert storage.read_json(path) == [1, 2]

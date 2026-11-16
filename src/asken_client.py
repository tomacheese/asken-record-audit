"""asken.jp にログインし、食事記録と食事写真を取得するクライアント。

`requests` だけを使い、ブラウザ自動化は使わない。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import TracebackType
from urllib.parse import urlparse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BASE_URL = "https://www.asken.jp"
MEAL_TYPES = ["breakfast", "lunch", "dinner", "sweets"]
MEAL_LABEL = {
    "breakfast": "朝食",
    "lunch": "昼食",
    "dinner": "夕食",
    "sweets": "間食",
}

EAT_DATAS_RE = re.compile(r"V2WspMeal\.eatDatas\s*=\s*(\{.*?\});", re.DOTALL)
CSRF_RE = re.compile(r'name="_csrfToken"\s+value="([^"]+)"')
PHOTO_PATH_RE = re.compile(r"/meal_photo/my_photo/[^\"'\s]+")


class LoginError(RuntimeError):
    """asken.jp への認証に失敗したときに送出する。"""


class AskenParseError(RuntimeError):
    """取得したページの構造が想定と異なるときに送出する。"""


@dataclass
class MealRecord:
    """1 つの日付・食事区分における、メニュー項目と写真 URL。"""

    meal_type: str
    meal_label: str
    items: list[dict] = field(default_factory=list)
    photo_urls: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        """メニュー項目か写真が 1 つでも記録されているか。"""
        return bool(self.items) or bool(self.photo_urls)

    @property
    def total_energy_kcal(self) -> int:
        """メニュー項目のエネルギー (kcal) の合計。数値として解釈できない値は無視する。"""
        total = 0
        for item in self.items:
            try:
                total += int(item.get("energy_kcal") or 0)
            except (ValueError, TypeError):
                pass
        return total


class AskenClient:
    """asken.jp 向けの、セッション付きクライアント。"""

    def __init__(self, email: str, password: str):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.email = email
        self.password = password

    def __enter__(self) -> AskenClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    def login(self) -> None:
        """asken.jp に認証し、失敗したら LoginError を送出する。

        すでに認証済みのセッションでは `GET /login` がトップページへリダイレクトされる。
        リダイレクトと、遷移先の「ログアウト」リンクの両方が揃ったときだけ、認証済みとみなす。
        """
        r = self.session.get(f"{BASE_URL}/login", timeout=30)
        r.raise_for_status()
        redirected_away_from_login = (
            r.history and urlparse(r.url).path.rstrip("/") != "/login"
        )
        if redirected_away_from_login and _has_logout_link(r.text):
            return
        m = CSRF_RE.search(r.text)
        if not m:
            raise LoginError("CSRF token not found on login page")

        r2 = self.session.post(
            f"{BASE_URL}/login",
            data={
                "_csrfToken": m.group(1),
                "CustomerMember[email]": self.email,
                "CustomerMember[passwd_plain]": self.password,
                "CustomerMember[autologin]": "0",
            },
            timeout=30,
        )
        r2.raise_for_status()
        if not _has_logout_link(r2.text):
            raise LoginError("Login failed (no logout link found in response)")

    def fetch_meal(self, meal_type: str, date: str) -> MealRecord:
        """1 つの日付・食事区分のメニュー項目と写真 URL を取得する。

        記録のない食事のページにも `eatDatas` の代入は (空で) 存在する。
        見つからなければ構造の変化とみなして AskenParseError を送出する。
        """
        if meal_type not in MEAL_LABEL:
            raise ValueError(f"Unknown meal type: {meal_type!r}")
        r = self.session.get(
            f"{BASE_URL}/wsp/meal/{meal_type}/{date}", timeout=30
        )
        r.raise_for_status()
        m = EAT_DATAS_RE.search(r.text)
        if not m:
            raise AskenParseError(
                "V2WspMeal.eatDatas not found on meal page: "
                f"{meal_type} {date}"
            )

        items = [
            {
                "item_hash": item_hash,
                "menu_id": item.get("menu_id"),
                "menu_name": item.get("menu_name"),
                "quantity": item.get("menu_quantity"),
                "energy_kcal": item.get("energy"),
            }
            for item_hash, item in json.loads(m.group(1)).items()
        ]

        photo_urls: dict[str, str] = {}
        for path in sorted(set(PHOTO_PATH_RE.findall(r.text))):
            parts = path.rstrip("/").split("/")
            if len(parts) >= 5:
                photo_urls[parts[-2]] = "/".join(parts[:-1]) + "/fit_380x380"

        return MealRecord(
            meal_type=meal_type,
            meal_label=MEAL_LABEL[meal_type],
            items=items,
            photo_urls=list(photo_urls.values()),
        )

    def download_photo(self, url_path: str) -> bytes:
        """食事の写真をダウンロードし、生のバイト列を返す。"""
        r = self.session.get(f"{BASE_URL}{url_path}", timeout=30)
        r.raise_for_status()
        return r.content


def _has_logout_link(text: str) -> bool:
    return "ログアウト" in text or "logout" in text.lower()

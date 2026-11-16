"""あすけんクライアントのテスト。ネットワークには出ず、セッションを偽物に差し替える。"""
import pytest

import asken_client
from asken_client import AskenClient, AskenParseError, LoginError, MealRecord

LOGIN_URL = "https://www.asken.jp/login"
LOGIN_PAGE = '<form><input name="_csrfToken" value="tok123"></form>'
LOGGED_IN_PAGE = '<a href="/logout">ログアウト</a>'

MEAL_PAGE = """
<script>
V2WspMeal.eatDatas = {
  "h1": {"menu_id": "1", "menu_name": "ご飯",
         "menu_quantity": "1", "energy": "156"},
  "h2": {"menu_id": "2", "menu_name": "味噌汁",
         "menu_quantity": "1", "energy": "40"}
};
</script>
<img src="/meal_photo/my_photo/aaa111/original">
<img src="/meal_photo/my_photo/aaa111/thumb">
<img src="/meal_photo/my_photo/bbb222/original">
"""

EMPTY_MEAL_PAGE = "<script>V2WspMeal.eatDatas = {};</script>"


class FakeResponse:
    def __init__(self, text="", content=b"", url="", history=None):
        self.text = text
        self.content = content
        self.url = url
        self.history = history or []

    def raise_for_status(self):
        pass


def login_page():
    return FakeResponse(LOGIN_PAGE, url=LOGIN_URL)


class FakeSession:
    def __init__(self, get_responses=(), post_responses=()):
        self.headers = {}
        self.get_responses = list(get_responses)
        self.post_responses = list(post_responses)
        self.get_calls = []
        self.post_calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)

    def close(self):
        self.closed = True


def make_client(session):
    client = AskenClient("user@example.com", "s3cret-pass")
    client.session = session
    return client


def test_login_posts_credentials_with_csrf_token():
    session = FakeSession(
        get_responses=[login_page()],
        post_responses=[FakeResponse(LOGGED_IN_PAGE)],
    )

    make_client(session).login()

    url, kwargs = session.post_calls[0]
    assert url == "https://www.asken.jp/login"
    assert kwargs["data"] == {
        "_csrfToken": "tok123",
        "CustomerMember[email]": "user@example.com",
        "CustomerMember[passwd_plain]": "s3cret-pass",
        "CustomerMember[autologin]": "0",
    }


def test_login_skips_post_when_already_logged_in():
    session = FakeSession(
        get_responses=[
            FakeResponse(
                LOGGED_IN_PAGE,
                url="https://www.asken.jp/",
                history=[FakeResponse()],
            )
        ],
    )

    make_client(session).login()

    assert session.post_calls == []


def test_login_without_csrf_token_raises():
    session = FakeSession(
        get_responses=[FakeResponse("<html></html>", url=LOGIN_URL)],
    )

    with pytest.raises(LoginError, match="CSRF token not found on login page"):
        make_client(session).login()


def test_login_without_logout_link_raises_without_leaking_credentials():
    session = FakeSession(
        get_responses=[login_page()],
        post_responses=[FakeResponse("<html>failed</html>")],
    )

    with pytest.raises(LoginError) as excinfo:
        make_client(session).login()

    assert str(excinfo.value) == (
        "Login failed (no logout link found in response)"
    )


def test_fetch_meal_parses_items_and_deduplicated_photos():
    session = FakeSession(get_responses=[FakeResponse(MEAL_PAGE)])

    meal = make_client(session).fetch_meal("lunch", "2026-10-23")

    assert session.get_calls[0][0] == (
        "https://www.asken.jp/wsp/meal/lunch/2026-10-23"
    )
    assert meal == MealRecord(
        meal_type="lunch",
        meal_label="昼食",
        items=[
            {
                "item_hash": "h1",
                "menu_id": "1",
                "menu_name": "ご飯",
                "quantity": "1",
                "energy_kcal": "156",
            },
            {
                "item_hash": "h2",
                "menu_id": "2",
                "menu_name": "味噌汁",
                "quantity": "1",
                "energy_kcal": "40",
            },
        ],
        photo_urls=[
            "/meal_photo/my_photo/aaa111/fit_380x380",
            "/meal_photo/my_photo/bbb222/fit_380x380",
        ],
    )


def test_fetch_meal_returns_empty_record_for_empty_meal():
    session = FakeSession(get_responses=[FakeResponse(EMPTY_MEAL_PAGE)])

    meal = make_client(session).fetch_meal("sweets", "2026-10-23")

    assert meal == MealRecord(
        meal_type="sweets", meal_label="間食", items=[], photo_urls=[]
    )
    assert meal.has_content is False


@pytest.mark.parametrize(
    "page",
    [
        "<html>maintenance</html>",
        "<script>V2WspMeal.init();</script>",
    ],
)
def test_fetch_meal_raises_when_page_structure_is_unexpected(page):
    session = FakeSession(get_responses=[FakeResponse(page)])

    with pytest.raises(AskenParseError) as excinfo:
        make_client(session).fetch_meal("dinner", "2026-10-23")

    assert str(excinfo.value) == (
        "V2WspMeal.eatDatas not found on meal page: dinner 2026-10-23"
    )


def test_fetch_meal_rejects_unknown_meal_type():
    session = FakeSession()

    with pytest.raises(ValueError, match="Unknown meal type: 'brunch'"):
        make_client(session).fetch_meal("brunch", "2026-10-23")

    assert session.get_calls == []


def test_total_energy_ignores_non_numeric_values():
    meal = MealRecord(
        meal_type="lunch",
        meal_label="昼食",
        items=[
            {"energy_kcal": "100"},
            {"energy_kcal": "abc"},
            {"energy_kcal": None},
            {"energy_kcal": 50},
        ],
    )

    assert meal.total_energy_kcal == 150


def test_download_photo_returns_bytes():
    photo = FakeResponse(content=b"\xff\xd8jpeg")
    session = FakeSession(get_responses=[photo])

    data = make_client(session).download_photo(
        "/meal_photo/my_photo/aaa111/fit_380x380"
    )

    assert data == b"\xff\xd8jpeg"
    assert session.get_calls[0][0] == (
        "https://www.asken.jp/meal_photo/my_photo/aaa111/fit_380x380"
    )


def test_context_manager_closes_session():
    client = AskenClient("u", "p")
    session = FakeSession()
    client.session = session

    with client:
        pass

    assert session.closed is True


def test_meal_types_and_labels_are_consistent():
    assert set(asken_client.MEAL_TYPES) == set(asken_client.MEAL_LABEL)

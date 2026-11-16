"""Azure OpenAI クライアントのテスト。ネットワークには出ず、セッションを偽物に差し替える。"""
import json

import pytest
import requests

from azure_client import AzureClient, AzureError

ENDPOINT = "https://example.test/openai/v1/"
API_KEY = "k-secret-value"
SCHEMA = {"type": "object"}
MESSAGES = [{"role": "user", "content": "hi"}]


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            return json.loads(self.text)
        return self._body


def ok(payload):
    return FakeResponse(
        200, {"choices": [{"message": {"content": json.dumps(payload)}}]}
    )


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def make_client(outcomes):
    sleeps = []
    session = FakeSession(outcomes)
    client = AzureClient(
        ENDPOINT, API_KEY, session=session, sleep=sleeps.append
    )
    return client, session, sleeps


def test_chat_json_sends_expected_request_and_parses_response():
    client, session, sleeps = make_client([ok({"a": 1})])

    result = client.chat_json("gpt-6-luna", MESSAGES, SCHEMA)

    assert result == {"a": 1}
    url, kwargs = session.calls[0]
    assert url == "https://example.test/openai/v1/chat/completions"
    assert kwargs["headers"] == {
        "api-key": API_KEY,
        "Content-Type": "application/json",
    }
    assert kwargs["timeout"] == 120
    assert kwargs["json"] == {
        "model": "gpt-6-luna",
        "messages": MESSAGES,
        "reasoning_effort": "medium",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "meal_diff",
                "strict": True,
                "schema": SCHEMA,
            },
        },
    }
    assert sleeps == []


def test_retries_on_429_then_succeeds():
    client, session, sleeps = make_client(
        [FakeResponse(429, text="slow down"), ok({"a": 1})]
    )

    assert client.chat_json("d", MESSAGES, SCHEMA) == {"a": 1}
    assert len(session.calls) == 2
    assert sleeps == [1]


def test_retries_on_5xx_and_timeout_then_succeeds():
    client, session, sleeps = make_client(
        [
            FakeResponse(503, text="unavailable"),
            requests.Timeout(),
            ok({"a": 1}),
        ]
    )

    assert client.chat_json("d", MESSAGES, SCHEMA) == {"a": 1}
    assert len(session.calls) == 3
    assert sleeps == [1, 2]


@pytest.mark.parametrize(
    "error",
    [requests.ConnectionError(), requests.exceptions.ChunkedEncodingError()],
)
def test_retries_on_transport_errors(error):
    client, session, sleeps = make_client([error, ok({"a": 1})])

    assert client.chat_json("d", MESSAGES, SCHEMA) == {"a": 1}
    assert sleeps == [1]


def test_raises_after_three_failed_attempts():
    client, session, sleeps = make_client(
        [
            FakeResponse(500, text="e1"),
            FakeResponse(502, text="e2"),
            FakeResponse(503, text="e3"),
        ]
    )

    with pytest.raises(AzureError) as excinfo:
        client.chat_json("d", MESSAGES, SCHEMA)

    assert str(excinfo.value) == (
        "Azure OpenAI request failed after 3 attempts: HTTP 503: e3"
    )
    assert len(session.calls) == 3
    assert sleeps == [1, 2]


def test_does_not_retry_on_other_4xx():
    client, session, sleeps = make_client(
        [FakeResponse(400, text="bad request")]
    )

    with pytest.raises(AzureError) as excinfo:
        client.chat_json("d", MESSAGES, SCHEMA)

    assert str(excinfo.value) == "HTTP 400: bad request"
    assert len(session.calls) == 1
    assert sleeps == []


def test_truncates_error_body_to_200_characters():
    client, _, _ = make_client([FakeResponse(400, text="x" * 500)])

    with pytest.raises(AzureError) as excinfo:
        client.chat_json("d", MESSAGES, SCHEMA)

    assert str(excinfo.value) == "HTTP 400: " + "x" * 200


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(200, text="not json"),
        FakeResponse(200, {"choices": []}),
        FakeResponse(200, {"choices": [{"message": {"content": "not json"}}]}),
    ],
)
def test_malformed_success_response_raises_without_retry(response):
    client, session, _ = make_client([response])

    with pytest.raises(
        AzureError, match="Unexpected response from deployment d"
    ):
        client.chat_json("d", MESSAGES, SCHEMA)

    assert len(session.calls) == 1


def test_api_key_is_not_exposed_in_errors_or_repr():
    client, _, _ = make_client([FakeResponse(401, text="unauthorized")])

    with pytest.raises(AzureError) as excinfo:
        client.chat_json("d", MESSAGES, SCHEMA)

    assert API_KEY not in str(excinfo.value)
    assert API_KEY not in repr(client)


def test_context_manager_closes_session():
    client, session, _ = make_client([])

    with client:
        pass

    assert session.closed is True

"""Azure OpenAI の chat completions を、構造化出力 (JSON スキーマ) で呼ぶクライアント。"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from types import TracebackType

import requests

logger = logging.getLogger("asken-record-audit")

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2)
REQUEST_TIMEOUT_SECONDS = 120
ERROR_BODY_LIMIT = 200


class AzureError(RuntimeError):
    """Azure OpenAI の呼び出しが失敗したときに送出する。"""


class AzureClient:
    """Azure OpenAI の `/chat/completions` を呼ぶクライアント。

    `endpoint` は `/openai/v1` までを含むベース URL。一時的な失敗 (通信エラー、
    HTTP 429、5xx) は最大 MAX_ATTEMPTS 回まで再試行する。
    `sleep` は再試行の待機に使う関数で、呼び出し側が割り込み可能なものを渡せる。
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], object] = time.sleep,
    ):
        self._url = f"{endpoint.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._session = session or requests.Session()
        self._sleep = sleep

    def __enter__(self) -> AzureClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def chat_json(
        self, deployment: str, messages: list[dict], schema: dict
    ) -> dict:
        """`schema` に従う JSON をモデルから受け取り、辞書として返す。

        失敗したら AzureError を送出する。例外のメッセージには、リクエストの
        ヘッダー (API キー) を含めない。
        """
        payload = {
            "model": deployment,
            "messages": messages,
            "reasoning_effort": "medium",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "meal_diff",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

        last_error = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._session.post(
                    self._url,
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as e:
                last_error = type(e).__name__
            else:
                if response.status_code == 200:
                    return _parse_content(response, deployment)
                body = response.text[:ERROR_BODY_LIMIT]
                last_error = f"HTTP {response.status_code}: {body}"
                if response.status_code != 429 and response.status_code < 500:
                    raise AzureError(last_error)

            if attempt < MAX_ATTEMPTS - 1:
                delay = BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Azure OpenAI request failed "
                    "(deployment=%s, attempt %s/%s): %s; "
                    "retrying in %s seconds",
                    deployment,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    last_error,
                    delay,
                )
                self._sleep(delay)

        raise AzureError(
            f"Azure OpenAI request failed after {MAX_ATTEMPTS} attempts: "
            f"{last_error}"
        )


def _parse_content(response: requests.Response, deployment: str) -> dict:
    try:
        return json.loads(response.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise AzureError(
            f"Unexpected response from deployment {deployment}: "
            f"{type(e).__name__}"
        ) from e

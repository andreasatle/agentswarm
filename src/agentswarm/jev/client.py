"""The Jev client: the only code that reads the Jev API key (proposal §16).

Ported from ~/Projects/Jev/jev_client.py and jev_api.ask. The base URL is fixed in code and every
request is checked to be HTTPS before it is sent, so there is no setting that could leak the key.
"""

import os
import time
from collections.abc import Callable
from typing import Any, Self

import httpx
from pydantic import BaseModel

from agentswarm.jev.api import JevResponse
from agentswarm.jev.spec import QuestionSet


class JevError(Exception):
    """Jev could not be reached, refused the request, or answered in an unexpected shape."""


class JevClient:
    BASE_URL = "https://api.typesafe.ai/v1"
    KEY_VARIABLE = "TYPESAFE_API_KEY"
    ERROR_BODY_LIMIT = 500  # characters of an error response kept in the exception
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})  # worth asking again; anything else is final

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,  # tests pass an httpx.MockTransport
        attempts: int = 3,
        backoff_s: float = 0.5,  # doubles after each failed attempt
        sleep: Callable[[float], None] = time.sleep,  # tests pass a recorder
    ) -> None:
        if not api_key:
            raise JevError(f"no Jev API key; set {self.KEY_VARIABLE}")
        self.model = model  # pin a concrete version (e.g. "jev-1.13.0") in production; see §15.1
        self._attempts = max(1, attempts)
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            event_hooks={"request": [self.require_https]},
            timeout=timeout_s,
            transport=transport,
        )

    @classmethod
    def from_env(cls, model: str, timeout_s: float = 30.0) -> Self:
        return cls(model, os.environ.get(cls.KEY_VARIABLE, ""), timeout_s)

    @staticmethod
    def require_https(request: httpx.Request) -> None:
        """Runs before every request. Refuses to send the key over anything but HTTPS."""
        if request.url.scheme != "https":
            raise JevError(f"refusing to send the API key over {request.url.scheme}: {request.url}")

    def decide[M: BaseModel](self, state: BaseModel, spec: type[M]) -> JevResponse[M]:
        """Ask the questions declared by `spec` about `state` (sent as a JSON object)."""
        body = {
            "model": self.model,
            "state": state.model_dump(mode="json"),
            "questions": QuestionSet.of(spec).model_dump(mode="json", exclude_none=True),
        }
        response = self._post(body)
        try:
            return JevResponse[spec].model_validate_json(response.text)
        except ValueError as error:
            raise JevError(f"unexpected Jev response for {spec.__name__}: {error}") from error

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        """POST with retries for timeouts, connection errors and the statuses in RETRY_STATUSES."""
        delay = self._backoff_s
        for attempt in range(1, self._attempts + 1):
            last = attempt == self._attempts
            try:
                response = self._http.post("/systemone", json=body)
            except httpx.TransportError as error:  # timeouts, refused connections, dropped connections
                if last:
                    raise JevError(f"could not reach Jev after {attempt} attempts: {error}") from error
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in self.RETRY_STATUSES or last:
                    raise JevError(
                        f"Jev returned HTTP {response.status_code}: {response.text[: self.ERROR_BODY_LIMIT]}"
                    )
            self._sleep(delay)
            delay *= 2
        raise AssertionError("unreachable: the last attempt returns or raises")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

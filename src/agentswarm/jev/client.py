"""The Jev client: the only code that reads the Jev API key (proposal §16).

Ported from ~/Projects/Jev/jev_client.py and jev_api.ask. The base URL is fixed in code and every
request is checked to be HTTPS before it is sent, so there is no setting that could leak the key.
"""

import os
from typing import Self

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

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,  # tests pass an httpx.MockTransport
    ) -> None:
        if not api_key:
            raise JevError(f"no Jev API key; set {self.KEY_VARIABLE}")
        self.model = model  # pin a concrete version (e.g. "jev-1.13.0") in production; see §15.1
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
        try:
            response = self._http.post("/systemone", json=body)
        except httpx.HTTPError as error:
            raise JevError(f"could not reach Jev: {error}") from error
        if response.status_code != 200:
            raise JevError(
                f"Jev returned HTTP {response.status_code}: {response.text[: self.ERROR_BODY_LIMIT]}"
            )
        try:
            return JevResponse[spec].model_validate_json(response.text)
        except ValueError as error:
            raise JevError(f"unexpected Jev response for {spec.__name__}: {error}") from error

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

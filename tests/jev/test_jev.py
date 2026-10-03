import json
import os
from typing import Annotated, Any, ClassVar

import httpx
import pytest
from pydantic import BaseModel

from agentswarm.jev.api import ChoiceAnswer, JevResponse, NoulAnswer, ScoreAnswer
from agentswarm.jev.client import JevClient, JevError
from agentswarm.jev.spec import Ask, Option, QuestionSet


class Team(Option):
    SCHEDULER = "scheduler", "Task scheduling, event loop"
    PROVIDERS = "providers", "LLM backends and API clients"


class Routing(BaseModel):
    team: Annotated[ChoiceAnswer[Team], Ask("Which team should own this task?")]
    too_large: Annotated[
        NoulAnswer, Ask("Is this task too large?", yes="It needs splitting", no="One branch is enough")
    ]
    drift: Annotated[
        ScoreAnswer, Ask("How well does it serve the north star?", levels=("Off", "Related", "Direct"))
    ]


class Task(BaseModel):
    text: str


class Replies:
    """A fake Jev server: records each request and answers with a fixed body."""

    ANSWERS: ClassVar[dict[str, Any]] = {
        "team": {
            "choice": "providers",
            "confidence": 0.9,
            "probabilities": {"scheduler": 0.1, "providers": 0.9},
        },
        "too_large": {"noul": 0.2},
        "drift": {"score": 2.0, "confidence": 0.8, "probabilities": {"0": 0.05, "1": 0.15, "2": 0.8}},
    }

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status
        self._body = body or {
            "model": "jev-1.13.0",
            "answers": self.ANSWERS,
            "usage": {"input_tokens": 120, "output_tokens": 3},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._body)

    def client(self) -> JevClient:
        return JevClient("jev-latest", "secret-key", transport=httpx.MockTransport(self.handle))


class TestQuestionSet:
    def test_builds_typed_questions_from_the_spec(self) -> None:
        questions = QuestionSet.of(Routing).model_dump(mode="json", exclude_none=True)
        assert questions == {
            "team": {
                "type": "choice",
                "instructions": "Which team should own this task?",
                "criteria": {
                    "scheduler": "Task scheduling, event loop",
                    "providers": "LLM backends and API clients",
                },
            },
            "too_large": {
                "type": "noul",
                "instructions": "Is this task too large?",
                "criteria": {"true": "It needs splitting", "false": "One branch is enough"},
            },
            "drift": {
                "type": "score",
                "instructions": "How well does it serve the north star?",
                "criteria": ["Off", "Related", "Direct"],
            },
        }

    def test_noul_without_criteria_omits_them(self) -> None:
        class Plain(BaseModel):
            done: Annotated[NoulAnswer, Ask("Done?")]

        assert QuestionSet.of(Plain).model_dump(mode="json", exclude_none=True) == {
            "done": {"type": "noul", "instructions": "Done?"}
        }

    def test_fields_without_ask_or_levels_are_errors(self) -> None:
        class NoAsk(BaseModel):
            done: NoulAnswer

        class NoLevels(BaseModel):
            score: Annotated[ScoreAnswer, Ask("How good?")]

        with pytest.raises(TypeError, match="missing Ask"):
            QuestionSet.of(NoAsk)
        with pytest.raises(TypeError, match="needs levels"):
            QuestionSet.of(NoLevels)


class TestJevClient:
    def test_sends_state_and_questions_and_parses_typed_answers(self) -> None:
        replies = Replies()
        with replies.client() as jev:
            response = jev.decide(Task(text="Add retry to the Ollama client"), Routing)

        (request,) = replies.requests
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer secret-key"
        body = json.loads(request.content)
        assert body["model"] == "jev-latest"
        assert body["state"] == {"text": "Add retry to the Ollama client"}
        assert set(body["questions"]) == {"team", "too_large", "drift"}

        assert isinstance(response, JevResponse)
        assert response.model == "jev-1.13.0"
        assert response.answers.team.choice is Team.PROVIDERS
        assert response.answers.too_large.noul == 0.2
        assert response.answers.drift.probabilities[2] == 0.8

    def test_http_errors_become_jev_errors_without_the_key(self) -> None:
        with Replies(status=401, body={"error": "bad key"}).client() as jev, pytest.raises(JevError) as error:
            jev.decide(Task(text="x"), Routing)
        assert "HTTP 401" in str(error.value)
        assert "secret-key" not in str(error.value)

    def test_answers_that_do_not_fit_the_spec_are_errors(self) -> None:
        body = {
            "model": "jev-1.13.0",
            "answers": {"team": "nonsense"},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        with Replies(body=body).client() as jev, pytest.raises(JevError, match="unexpected Jev response"):
            jev.decide(Task(text="x"), Routing)

    def test_refuses_anything_but_https(self) -> None:
        with pytest.raises(JevError, match="refusing"):
            JevClient.require_https(httpx.Request("POST", "http://api.typesafe.ai/v1/systemone"))

    def test_requires_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(JevClient.KEY_VARIABLE, raising=False)
        with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
            JevClient.from_env("jev-latest")


@pytest.mark.jev
class TestJevLive:
    def test_a_real_noul_question(self) -> None:
        class Check(BaseModel):
            is_greeting: Annotated[NoulAnswer, Ask("Is this text a greeting?")]

        with JevClient.from_env(os.environ["AGENTSWARM_JEV_MODEL"]) as jev:
            response = jev.decide(Task(text="Hello there, nice to meet you!"), Check)
        assert response.answers.is_greeting.noul > 0.5


class Flaky:
    """A fake Jev that fails with the given statuses first, then answers."""

    def __init__(self, *statuses: int) -> None:
        self._statuses = list(statuses)
        self.sleeps: list[float] = []
        self.calls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self._statuses:
            return httpx.Response(self._statuses.pop(0), text="busy")
        return Replies().handle(request)

    def client(self, attempts: int = 3) -> JevClient:
        return JevClient(
            "jev-latest",
            "k",
            transport=httpx.MockTransport(self.handle),
            attempts=attempts,
            sleep=self.sleeps.append,
        )


class TestJevRetries:
    def test_retries_busy_answers_with_growing_waits(self) -> None:
        flaky = Flaky(503, 429)
        response = flaky.client().decide(Task(text="x"), Routing)
        assert response.model == "jev-1.13.0"
        assert (flaky.calls, flaky.sleeps) == (3, [0.5, 1.0])

    def test_gives_up_after_the_last_attempt(self) -> None:
        flaky = Flaky(503, 503, 503)
        with pytest.raises(JevError, match="HTTP 503"):
            flaky.client().decide(Task(text="x"), Routing)
        assert flaky.calls == 3

    def test_does_not_retry_requests_that_cannot_succeed(self) -> None:
        flaky = Flaky(400)
        with pytest.raises(JevError, match="HTTP 400"):
            flaky.client().decide(Task(text="x"), Routing)
        assert (flaky.calls, flaky.sleeps) == (1, [])

    def test_retries_connection_failures(self) -> None:
        calls: list[int] = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ConnectError("refused")
            return Replies().handle(request)

        client = JevClient("jev-latest", "k", transport=httpx.MockTransport(handle), sleep=lambda _: None)
        assert client.decide(Task(text="x"), Routing).model == "jev-1.13.0"
        assert len(calls) == 2

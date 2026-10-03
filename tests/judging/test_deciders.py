from pathlib import Path
from typing import Literal

import httpx
import pytest

from agentswarm.core.config import ProjectConfig
from agentswarm.jev.client import JevClient, JevError
from agentswarm.judging.deciders import (
    DeciderError,
    FakeDecider,
    FallbackDecider,
    JevDecider,
    LLMAnswers,
    LLMDecider,
    LLMQuestions,
    QuestionAnswer,
)
from agentswarm.judging.judges import ChangeReadiness, JudgeState
from agentswarm.llm.workers import FakeWorker, Toolbox
from agentswarm.project import Project
from tests.support.projects import Projects
from tests.support.samples import Judged

STATE = JudgeState(north_star="n", task="t", must_fix=(), diff="")


class TestFakeDecider:
    def test_plays_answers_in_order_then_repeats_the_last(self) -> None:
        decider = FakeDecider([Judged.incomplete(), Judged.passing()])
        answers = [decider.decide(STATE, ChangeReadiness).answers for _ in range(3)]
        assert answers == [Judged.incomplete(), Judged.passing(), Judged.passing()]
        assert len(decider.calls) == 3

    def test_no_answers_or_the_wrong_spec_is_a_decider_error(self) -> None:
        with pytest.raises(DeciderError, match="no scripted answers"):
            FakeDecider().decide(STATE, ChangeReadiness)
        with pytest.raises(DeciderError, match="holds answers for ChangeReadiness, asked for JudgeState"):
            FakeDecider([Judged.passing()]).decide(STATE, JudgeState)


class TestJevDecider:
    def test_returns_answers_and_the_version_that_answered(self) -> None:
        body = {
            "model": "jev-1.13.0",
            "answers": Judged.passing().model_dump(mode="json"),
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        answered = JevDecider(JevClient("jev-latest", "key", transport=transport)).decide(
            STATE, ChangeReadiness
        )
        assert answered.answers == Judged.passing()
        assert answered.model == "jev-1.13.0"

    def test_jev_failures_become_decider_errors(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(503, text="busy"))
        decider = JevDecider(JevClient("jev-latest", "key", transport=transport, sleep=lambda _: None))
        with pytest.raises(DeciderError, match="HTTP 503"):
            decider.decide(STATE, ChangeReadiness)


class TestProjectJudgeWiring:
    def test_a_judged_gate_without_a_decider_needs_the_jev_key_at_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(JevClient.KEY_VARIABLE, raising=False)
        config = Projects.CONFIG | {"gates": [Projects.CONFIG["gates"][0] | {"judge": "ChangeReadiness"}]}
        with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
            Project.open(ProjectConfig.model_validate(config), tmp_path)

    def test_an_unknown_judge_fails_at_open(self, tmp_path: Path) -> None:
        config = Projects.CONFIG | {"gates": [Projects.CONFIG["gates"][0] | {"judge": "NoSuchJudge"}]}
        with pytest.raises(ValueError, match="unknown judge"):
            Projects.open_config(ProjectConfig.model_validate(config), tmp_path)


class LLMReplies:
    @staticmethod
    def worker(**answers: Literal["yes", "no", "unsure"]) -> FakeWorker:
        def step(toolbox: Toolbox | None) -> LLMAnswers:
            return LLMAnswers(answers=tuple(QuestionAnswer(question=q, answer=a) for q, a in answers.items()))

        return FakeWorker([step])


class TestLLMDecider:
    def test_maps_yes_no_unsure_to_fixed_probabilities_and_marks_itself(self) -> None:
        worker = LLMReplies.worker(in_scope="yes", tests_relevant="unsure", complete="no")
        answered = LLMDecider(worker, "ollama:m").decide(STATE, ChangeReadiness)
        assert (
            answered.answers.in_scope.noul,
            answered.answers.tests_relevant.noul,
            answered.answers.complete.noul,
        ) == (
            0.95,
            0.5,
            0.05,
        )
        assert answered.model == "llm:ollama:m" and LLMDecider.is_llm(answered.model)
        ((_, payload),) = worker.calls
        assert isinstance(payload, LLMQuestions) and set(payload.questions) == {
            "in_scope",
            "tests_relevant",
            "complete",
        }

    def test_unanswered_questions_are_an_error(self) -> None:
        with pytest.raises(DeciderError, match="unanswered"):
            LLMDecider(LLMReplies.worker(in_scope="yes"), "m").decide(STATE, ChangeReadiness)

    async def test_works_inside_a_running_event_loop(self) -> None:
        worker = LLMReplies.worker(in_scope="yes", tests_relevant="yes", complete="yes")
        assert LLMDecider(worker, "m").decide(STATE, ChangeReadiness).answers.complete.noul == 0.95


class TestFallbackDecider:
    def test_uses_the_fallback_only_when_the_primary_fails(self) -> None:
        fallback = FakeDecider([Judged.incomplete()])
        assert (
            FallbackDecider(FakeDecider([Judged.passing()]), fallback).decide(STATE, ChangeReadiness).answers
            == Judged.passing()
        )
        assert fallback.calls == []
        answered = FallbackDecider(FakeDecider(), fallback).decide(STATE, ChangeReadiness)
        assert answered.answers == Judged.incomplete()

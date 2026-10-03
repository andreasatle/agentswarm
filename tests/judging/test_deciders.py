from pathlib import Path

import httpx
import pytest

from agentswarm.core.config import ProjectConfig
from agentswarm.jev.client import JevClient, JevError
from agentswarm.judging.deciders import DeciderError, FakeDecider, JevDecider
from agentswarm.judging.judges import JudgeState, RawToExperimental
from agentswarm.project import Project
from tests.support.projects import Projects
from tests.support.samples import Judged

STATE = JudgeState(north_star="n", task="t", must_fix=(), diff="")


class TestFakeDecider:
    def test_plays_answers_in_order_then_repeats_the_last(self) -> None:
        decider = FakeDecider([Judged.incomplete(), Judged.passing()])
        answers = [decider.decide(STATE, RawToExperimental).answers for _ in range(3)]
        assert answers == [Judged.incomplete(), Judged.passing(), Judged.passing()]
        assert len(decider.calls) == 3

    def test_no_answers_or_the_wrong_spec_is_a_decider_error(self) -> None:
        with pytest.raises(DeciderError, match="no scripted answers"):
            FakeDecider().decide(STATE, RawToExperimental)
        with pytest.raises(DeciderError, match="asked for JudgeState"):
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
            STATE, RawToExperimental
        )
        assert answered.answers == Judged.passing()
        assert answered.model == "jev-1.13.0"

    def test_jev_failures_become_decider_errors(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(503, text="busy"))
        decider = JevDecider(JevClient("jev-latest", "key", transport=transport))
        with pytest.raises(DeciderError, match="HTTP 503"):
            decider.decide(STATE, RawToExperimental)


class TestProjectJudgeWiring:
    def test_a_judged_gate_without_a_decider_needs_the_jev_key_at_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(JevClient.KEY_VARIABLE, raising=False)
        config = Projects.CONFIG | {"gates": [Projects.CONFIG["gates"][0] | {"judge": "RawToExperimental"}]}
        with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
            Project.open(ProjectConfig.model_validate(config), tmp_path)

    def test_an_unknown_judge_fails_at_open(self, tmp_path: Path) -> None:
        config = Projects.CONFIG | {"gates": [Projects.CONFIG["gates"][0] | {"judge": "NoSuchJudge"}]}
        with pytest.raises(ValueError, match="unknown judge"):
            Projects.open_config(ProjectConfig.model_validate(config), tmp_path)

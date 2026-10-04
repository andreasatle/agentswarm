import subprocess
from pathlib import Path

import pytest
import yaml

from agentswarm.core.config import JevMode
from agentswarm.evaluation.faults import Variant
from agentswarm.evaluation.mutation import MutationTesting
from agentswarm.evaluation.run import EvaluationRun
from agentswarm.evaluation.suite import HiddenTests, TaskSuite
from agentswarm.gating.checks import CheckStatus
from agentswarm.judging.audit import GateAudit
from agentswarm.judging.decisions import InMemoryDecisions, Outcomes, OutcomeSource
from agentswarm.workspace.sandbox import LocalSandbox
from tests.support.suites import EXAMPLES, EXECUTOR_ONLY, PYTEST, FarewellSuite, GreeterSuite, TinySuite


class TestGreeterSuite:
    """Hidden tests are ground truth, so they are checked: right answers pass, the untouched seed fails."""

    @pytest.mark.parametrize("task_id", [t.id for t in GreeterSuite.all_tasks()])
    def test_reference_solution_passes_and_seed_fails(self, tmp_path: Path, task_id: str) -> None:
        task = GreeterSuite.task(task_id)
        reference = task.reference["greeter.py"].read_text()
        seed = (EXAMPLES / "seed" / "greeter.py").read_text()
        assert GreeterSuite.hidden_tests_pass(tmp_path / "ref", task, reference)
        assert not GreeterSuite.hidden_tests_pass(tmp_path / "seed", task, seed)

    def test_has_enough_tasks_including_multi_part_ones(self) -> None:
        assert len(GreeterSuite.SUITE.tasks) >= 10 and len(GreeterSuite.HARD.tasks) >= 10
        assert len({t.id for t in GreeterSuite.all_tasks()}) == len(GreeterSuite.all_tasks())  # ids unique
        assert {"farewell-and-shout", "robust-hello"} <= set(GreeterSuite.ids())


class TestTaskSuite:
    def test_load_resolves_paths_against_the_suite_file(self) -> None:
        suite = GreeterSuite.SUITE
        assert suite.project == (EXAMPLES / "evaluation" / "project.yaml").resolve()
        assert all(h.is_absolute() and h.is_file() for t in suite.tasks for h in t.hidden_tests)

    def test_project_config_puts_the_judge_on_the_first_gate_in_shadow_mode(self) -> None:
        config = GreeterSuite.SUITE.project_config()
        assert config.gates[0].judge == "ChangeReadiness"
        assert config.jev.mode is JevMode.SHADOW

    def test_missing_files_and_duplicate_ids_are_errors(self, tmp_path: Path) -> None:
        (tmp_path / "suite.yaml").write_text(
            yaml.safe_dump(
                {"project": "nope.yaml", "tasks": [{"id": "a", "task": "t", "hidden_tests": ["x.py"]}]}
            )
        )
        with pytest.raises(ValueError, match="missing files"):
            TaskSuite.load(tmp_path / "suite.yaml")
        task = {"id": "a", "task": "t", "hidden_tests": ["x.py"]}
        with pytest.raises(ValueError, match="duplicate task ids"):
            TaskSuite.model_validate({"project": "p.yaml", "tasks": [task, task]})


class TestEvaluationRun:
    async def test_hidden_tests_become_outcomes_for_complete(self, tmp_path: Path) -> None:
        suite = TinySuite.write(tmp_path)
        decisions = InMemoryDecisions()
        done, half = await EvaluationRun(
            suite, tmp_path / "run", decisions, TinySuite.services, EXECUTOR_ONLY
        ).run()

        assert (done.task, done.level, done.hidden_tests_passed) == ("done", "experimental", (True,))
        assert (half.task, half.level, half.hidden_tests_passed) == ("half-done", "experimental", (False,))
        first, second = decisions.all()
        assert first.mode is JevMode.SHADOW
        complete, relevant = decisions.outcomes(first.id)
        assert (complete.field, complete.truth, complete.source) == (
            "complete",
            True,
            OutcomeSource.HIDDEN_TESTS,
        )
        # The scripted executor in this test adds no tests, so mutation testing finds them not relevant.
        assert (relevant.field, relevant.truth, relevant.source) == (
            "tests_relevant",
            False,
            OutcomeSource.MUTATION,
        )
        assert decisions.outcomes(second.id)[0].truth is False

    async def test_hidden_tests_never_reach_the_repository(self, tmp_path: Path) -> None:
        suite = TinySuite.write(tmp_path)
        await EvaluationRun(
            suite, tmp_path / "run", InMemoryDecisions(), TinySuite.services, EXECUTOR_ONLY
        ).run()
        for task in ("done", "half-done"):
            repo = tmp_path / "run" / task / "executor" / "repo"
            tracked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    "--all",
                    "--name-only",
                    "--format=",
                ],  # every file ever committed
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert "greeter.py" in tracked
            assert HiddenTests.DIRECTORY not in tracked

    async def test_a_used_workdir_is_refused(self, tmp_path: Path) -> None:
        suite = TinySuite.write(tmp_path)
        (tmp_path / "run" / "done" / "executor").mkdir(parents=True)
        with pytest.raises(FileExistsError, match="new workdir"):
            await EvaluationRun(
                suite, tmp_path / "run", InMemoryDecisions(), TinySuite.services, EXECUTOR_ONLY
            ).run()


class TestScriptedVariants:
    async def test_known_truths_and_mutation_agree_with_the_construction(self, tmp_path: Path) -> None:
        suite = FarewellSuite.write(tmp_path)
        decisions = InMemoryDecisions()
        variants = (Variant.REFERENCE, Variant.OUT_OF_SCOPE, Variant.TRIVIAL_TESTS)
        results = await EvaluationRun(
            suite, tmp_path / "run", decisions, FarewellSuite.services, variants
        ).run()

        truths: dict[Variant, dict[str, tuple[bool, OutcomeSource]]] = {}
        for result in results:
            (decision,) = result.decisions
            outcomes = decisions.outcomes(decision)
            truths[result.variant] = {
                field: (best.truth, best.source)
                for field in ("complete", "in_scope", "tests_relevant")
                if (best := Outcomes.strongest(outcomes, field)) is not None
            }
            assert result.hidden_tests_passed == (True,)  # every variant writes the reference solution

        assert truths[Variant.REFERENCE]["in_scope"] == (True, OutcomeSource.PLANTED)
        assert truths[Variant.OUT_OF_SCOPE]["in_scope"] == (False, OutcomeSource.PLANTED)
        assert truths[Variant.TRIVIAL_TESTS]["tests_relevant"] == (False, OutcomeSource.PLANTED)
        assert truths[Variant.TRIVIAL_TESTS]["complete"] == (
            False,
            OutcomeSource.PLANTED,
        )  # tests were asked for
        # Mutation testing reaches the same verdicts on its own:
        by_variant = {r.variant: r for r in results}
        assert (by_variant[Variant.REFERENCE].mutation_scores[0] or 0) >= MutationTesting.MIN_SCORE
        assert by_variant[Variant.TRIVIAL_TESTS].mutation_scores == (0.0,)

    def test_scripted_variants_need_reference_solutions(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="need reference solutions"):
            EvaluationRun(
                TinySuite.write(tmp_path), tmp_path / "run", InMemoryDecisions(), TinySuite.services
            )


class TestHiddenTestStatus:
    def test_a_missing_function_is_incomplete_not_an_error(self, tmp_path: Path) -> None:
        # pytest exits 2 when the code under test can't be imported: that is a real "no" for complete.
        task = GreeterSuite.task("banner")
        (tmp_path / "greeter.py").write_text((EXAMPLES / "seed" / "greeter.py").read_text())
        (tmp_path / "_hidden").mkdir()
        (tmp_path / "_hidden" / "test_banner.py").write_text(task.hidden_tests[0].read_text())
        result = LocalSandbox().run((*PYTEST, "_hidden"), tmp_path, 60)
        assert result.exit_code == 2
        codes = TaskSuite.model_fields["test_failure_exit_codes"].default
        assert CheckStatus.of(result, codes) is CheckStatus.FAILED


class TestEvaluationShape:
    def test_scripted_variants_refuse_a_project_with_other_teams(self, tmp_path: Path) -> None:
        suite = FarewellSuite.write(tmp_path)
        config = yaml.safe_load((tmp_path / "project.yaml").read_text())
        config["teams"].append(
            {"use": "red_team", "watches": "experimental", "model": "coder", "checks": ["unit_tests"]}
        )
        (tmp_path / "project.yaml").write_text(yaml.safe_dump(config))
        with pytest.raises(ValueError, match="only an executor"):
            EvaluationRun(
                suite, tmp_path / "run", InMemoryDecisions(), FarewellSuite.services, [Variant.REFERENCE]
            )

    def test_a_judge_that_doesnt_ask_what_evaluation_scores_is_refused(self, tmp_path: Path) -> None:
        suite = FarewellSuite.write(tmp_path).model_copy(update={"judge": "SizeCheck"})
        with pytest.raises(ValueError, match="which evaluation scores"):
            EvaluationRun(
                suite, tmp_path / "run", InMemoryDecisions(), FarewellSuite.services, [Variant.EXECUTOR]
            )


class TestActiveEvaluation:
    async def test_an_active_gate_is_audited_against_the_planted_truths(self, tmp_path: Path) -> None:
        suite = FarewellSuite.write(tmp_path)
        decisions = InMemoryDecisions()
        variants = (Variant.REFERENCE, Variant.TRIVIAL_TESTS)
        run = EvaluationRun(
            suite, tmp_path / "run", decisions, FarewellSuite.services, variants, active=["gate:*"]
        )
        await run.run()
        assert {d.mode for d in decisions.all()} == {JevMode.ACTIVE}
        audit = GateAudit.of(decisions)
        # The fake judge passes everything: right for the reference, wrong for the trivial tests.
        assert (audit.false_promotions.errors, audit.false_promotions.of) == (1, 2)

from pathlib import Path

from pydantic import BaseModel

from agentswarm.board.artifacts import ArtifactFactory
from agentswarm.core.config import JevMode
from agentswarm.core.domain import ArtifactCreated, ArtifactId, Escalated, Event, Sized
from agentswarm.jev.api import NoulAnswer
from agentswarm.judging.deciders import FakeDecider
from agentswarm.judging.judgement import Judgement
from agentswarm.judging.judges import Judge, SizeCheck, SplitCheck
from agentswarm.judging.policy import Policy
from agentswarm.llm.workers import FakeWorker, Step, Toolbox
from agentswarm.teams.decomposer import ChildTask, Decomposer, Split
from agentswarm.teams.executor import FakeExecutor
from agentswarm.teams.runner import Relevance
from tests.support.harness import Harness
from tests.support.samples import Samples


class Sizing:
    """A harness with one artifact, and a decomposer whose judges answer as scripted."""

    def __init__(
        self,
        tmp_path: Path,
        fits: float,
        smaller: float = 0.95,
        mode: JevMode = JevMode.ACTIVE,
        split_mode: JevMode | None = None,
    ) -> None:
        self.h = Harness(tmp_path)
        self.artifact = self.h.create(task="Build a greeting library with farewells and shouting")
        self.decider = FakeDecider(
            [SizeCheck(fits=NoulAnswer(noul=fits)), SplitCheck(smaller=NoulAnswer(noul=smaller))]
        )
        self.mode = mode
        self.split_mode = split_mode or mode

    def judgement(self, spec: type[BaseModel], mode: JevMode | None = None) -> Judgement:
        judge = Judge(spec)
        return Judgement(
            judge, Policy(judge), self.decider, self.h.decisions, mode or self.mode, self.h.clock
        )

    def decomposer(self, *steps: Step[BaseModel], max_depth: int = 3, build: bool = False) -> Decomposer:
        return Decomposer(
            Samples.RAW,
            FakeWorker(steps),
            self.judgement(SizeCheck),
            self.judgement(SplitCheck, self.split_mode),
            ArtifactFactory(self.h.board, Harness.LADDER),
            max_depth,
            build,
        )

    async def run(self, decomposer: Decomposer) -> list[Event]:
        assert await self.h.runner(decomposer).step() == 1
        return list(self.h.board.current().history(self.artifact.id))


def split_into(*tasks: str) -> Step[BaseModel]:
    def step(toolbox: Toolbox | None) -> Split:
        return Split(children=tuple(ChildTask(task=t) for t in tasks))

    return step


class TestDecomposer:
    async def test_a_task_that_fits_is_sized_and_left_to_the_executor(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.95)
        history = await s.run(s.decomposer())
        assert next(e for e in history if isinstance(e, Sized)).fits

    async def test_a_task_too_large_becomes_an_umbrella_over_its_children(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05)
        await s.run(s.decomposer(split_into("Add farewell(name)", "Add shout(name)")))
        views = s.h.board.current()
        assert views.is_umbrella(s.artifact.id)
        children = views.children(s.artifact.id)
        assert [(c.id, c.task, c.parent) for c in children] == [
            ("a-0001.1", "Add farewell(name)", s.artifact.id),
            ("a-0001.2", "Add shout(name)", s.artifact.id),
        ]
        assert views.depth(children[0].id) == 1
        assert [d.point for d in s.h.decisions.all()] == ["size:raw", "split:raw"]

    async def test_shadow_mode_logs_but_never_splits(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05, mode=JevMode.SHADOW)
        history = await s.run(s.decomposer())
        (sized,) = [e for e in history if isinstance(e, Sized)]
        assert sized.fits and sized.decision is not None

    async def test_a_split_that_isnt_clearly_smaller_escalates(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05, smaller=0.5)
        history = await s.run(s.decomposer(split_into("The same task", "The same task again")))
        assert any(isinstance(e, Escalated) and "not clearly smaller" in e.reason for e in history)
        assert not any(isinstance(e, ArtifactCreated) and e.artifact.parent for e in history)

    async def test_a_shadow_split_check_never_blocks_an_active_size_check(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05, smaller=0.5, split_mode=JevMode.SHADOW)
        history = await s.run(s.decomposer(split_into("Greetings", "Farewells")))
        assert not any(isinstance(e, Escalated) for e in history)
        assert any(isinstance(e, Sized) and not e.fits for e in history)

    async def test_the_depth_limit_escalates_instead_of_splitting_again(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05)  # every task looks too large
        await s.run(s.decomposer(split_into("Part a", "Part b"), max_depth=1))
        child = s.h.board.current().children(s.artifact.id)[0]  # depth 1: at the limit
        assert await s.h.runner(s.decomposer(max_depth=1)).step() == 2  # sizes both children
        assert any(
            isinstance(e, Escalated) and "already split 1 times" in e.reason
            for e in s.h.board.current().history(child.id)
        )

    async def test_at_the_depth_limit_it_can_build_instead(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05)
        await s.run(s.decomposer(split_into("Part a", "Part b"), max_depth=1, build=True))
        child = s.h.board.current().children(s.artifact.id)[0]
        assert await s.h.runner(s.decomposer(max_depth=1, build=True)).step() == 2
        history = s.h.board.current().history(child.id)
        assert not any(isinstance(e, Escalated) for e in history)
        assert any(isinstance(e, Sized) and e.fits for e in history)

    def test_fix_artifacts_are_not_sized(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.95)
        assert s.decomposer().relevant(s.h.contexts.build(s.artifact.id)) is Relevance.YES
        fix = ArtifactFactory(s.h.board, Harness.LADDER).create(
            "fix", Samples.EXECUTOR, s.artifact.id, fixes=Samples.finding().id
        )
        assert s.decomposer().relevant(s.h.contexts.build(fix.id)) is Relevance.NO


class TestExecutorWaitsForSizing:
    async def test_executor_waits_until_sized_and_never_works_on_umbrellas(self, tmp_path: Path) -> None:
        s = Sizing(tmp_path, fits=0.05)
        executor = FakeExecutor(Samples.RAW, requires_sizing=True)
        assert executor.relevant(s.h.contexts.build(s.artifact.id)) is Relevance.NO  # not sized yet
        await s.run(s.decomposer(split_into("Add farewell", "Add shout")))
        assert executor.relevant(s.h.contexts.build(s.artifact.id)) is Relevance.NO  # an umbrella
        child = ArtifactId("a-0001.1")
        s.h.board.append([Sized(artifact=child, fits=True)])
        assert executor.relevant(s.h.contexts.build(child)) is Relevance.YES

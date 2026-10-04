"""Human spot checks: do the automatic outcome sources agree with a person? (Phase 2 design, Q2)

Labelling is not the main source of truth; it checks the sources that are. A spot check samples
decisions that already have automatic outcomes, asks a person each question **without showing Jev's
answer or the automatic truth first** (so the label isn't anchored on them), and then reports how often
the person agreed with the automatic source.
"""

import random
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from agentswarm.core.clock import Clock
from agentswarm.judging.decisions import Decision, DecisionStore, Outcome, Outcomes, OutcomeSource
from agentswarm.judging.judges import JudgeCatalog


class Disagreement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: str
    field: str
    human: bool
    automatic: bool
    source: OutcomeSource


class SpotCheckSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    labelled: int
    compared: int  # labels that had an automatic outcome to compare with
    disagreements: tuple[Disagreement, ...]

    def render(self) -> str:
        if not self.compared:
            return f"{self.labelled} labels recorded; none had an automatic outcome to compare with."
        agreed = self.compared - len(self.disagreements)
        lines = [
            f"{self.labelled} labels recorded; agreed with the automatic source {agreed}/{self.compared}."
        ]
        lines += [
            f"  {d.decision} {d.field}: you said {'yes' if d.human else 'no'}, {d.source} said "
            f"{'yes' if d.automatic else 'no'}"
            for d in self.disagreements
        ]
        return "\n".join(lines)


class SpotCheck:
    DIFF_SHOWN = 4000  # characters of the diff shown per decision

    def __init__(
        self,
        store: DecisionStore,
        judges: JudgeCatalog,
        ask: Callable[[str], str],  # e.g. input
        show: Callable[[str], None],  # e.g. print
        clock: Clock,
        seed: int = 0,
    ) -> None:
        self._store = store
        self._judges = judges
        self._ask = ask
        self._show = show
        self._clock = clock
        self._random = random.Random(seed)

    def candidates(self, limit: int) -> list[Decision]:
        """A reproducible random sample of decisions with automatic outcomes and no human labels yet.

        Only decisions asked with the judge's current wording: a person answers today's questions, and a
        label must answer the question that was judged (CODEX_AUDIT.md F10).
        """
        eligible = [
            d
            for d in self._store.all()
            if (outcomes := self._store.outcomes(d.id))
            and not any(o.source is OutcomeSource.HUMAN for o in outcomes)
            and d.judge_version == self._judges.get(d.judge).version
        ]
        return self._random.sample(eligible, min(limit, len(eligible)))

    def run(self, limit: int) -> SpotCheckSummary:
        labelled, compared = 0, 0
        disagreements: list[Disagreement] = []
        for decision in self.candidates(limit):
            self._show_decision(decision)
            judge = self._judges.get(decision.judge)
            for question in judge.questions:
                answer = self._answer(f"{question.ask.instructions} [y/n/s=skip/q=quit] ")
                if answer == "q":
                    return SpotCheckSummary(
                        labelled=labelled, compared=compared, disagreements=tuple(disagreements)
                    )
                if answer == "s":
                    continue
                human = answer == "y"
                automatic = Outcomes.strongest(self._store.outcomes(decision.id), question.field)
                self._store.add_outcome(
                    Outcome(
                        decision=decision.id,
                        field=question.field,
                        truth=human,
                        source=OutcomeSource.HUMAN,
                        detail="spot check",
                        at=self._clock.now(),
                    )
                )
                labelled += 1
                revealed = f"    Jev: p(yes) = {decision.p_yes(question.field):.2f}"
                if automatic is not None:
                    compared += 1
                    revealed += f"; {automatic.source}: {'yes' if automatic.truth else 'no'}"
                    if automatic.truth != human:
                        disagreements.append(
                            Disagreement(
                                decision=decision.id,
                                field=question.field,
                                human=human,
                                automatic=automatic.truth,
                                source=automatic.source,
                            )
                        )
                self._show(revealed)
        return SpotCheckSummary(labelled=labelled, compared=compared, disagreements=tuple(disagreements))

    def _show_decision(self, decision: Decision) -> None:
        state = decision.state
        diff = state.diff[: self.DIFF_SHOWN] + (
            "\n[diff shortened]" if len(state.diff) > self.DIFF_SHOWN else ""
        )
        must_fix = "".join(f"\n  must fix: {f}" for f in state.must_fix)
        self._show(f"\n=== {decision.id} · {decision.point}\nTask: {state.task}{must_fix}\n\n{diff}\n")

    def _answer(self, prompt: str) -> str:
        while True:
            answer = self._ask(prompt).strip().lower()
            if answer in ("y", "n", "s", "q"):
                return answer
            self._show("    please answer y, n, s or q")

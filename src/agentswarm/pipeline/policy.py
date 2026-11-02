"""The decisions a document run makes without asking, in one file (pipeline/policy.yaml): what Claude decided
by hand during a chat before (the user, 2026-10-19: "I'm a bit uncomfortable that you make all the decisions
about what to run"). Changing a decision is editing a number; each run records the policy it used."""

from pathlib import Path
from typing import ClassVar, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Estimates(BaseModel):
    """Expected costs, from the runs of 2026-10-18/19, to plan a run and to stop before a stage that wouldn't
    fit the budget (estimates, not limits)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    find: float = 0.06  # the picker on the planner model, 40 candidates (0.04-0.05 measured)
    write_per_1000_words: float = 0.05  # 0.19-0.29 for 6,000-9,000 words
    audit: float = 0.08  # 20 statements on the referee
    review_per_section: float = 0.18  # one facts round: $1.75 for 10 sections

    def write(self, words: int) -> float:
        return self.write_per_1000_words * words / 1000

    def review(self, sections: int) -> float:
        return self.review_per_section * sections


class Policy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    papers: int = Field(default=5, gt=0)  # sources the finder picks
    words: int = Field(default=5000, gt=0)  # the document's length cap, unless the run says otherwise
    review_threshold: float = Field(default=0.75, gt=0, le=1)  # fully supported citations wanted
    review_rounds: int = Field(default=1, ge=0)  # facts rounds at most
    audit_sample: int = Field(default=20, gt=0)
    audit_seed: int = 1
    estimates: Estimates = Estimates()
    goal: str  # the brief's template: {topic} and {reader}

    DEFAULT: ClassVar[Path] = Path(__file__).with_name("policy.yaml")

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        return cls.model_validate(yaml.safe_load((path or cls.DEFAULT).read_text()))

    def brief(self, topic: str, reader: str) -> str:
        return self.goal.format(topic=topic, reader=reader)

    def plan(self, words: int, sections: int = 10) -> str:
        """The run's expected costs, step by step, for the user before it starts."""
        e = self.estimates
        review = e.review(sections) + e.audit
        return (
            f"sources ${e.find:.2f}, writing ${e.write(words):.2f}, citation audit ${e.audit:.2f}; "
            f"if fewer than {self.review_threshold:.0%} of citations hold up, a fact review ${review:.2f}"
        )

    def expected(self, words: int, sections: int = 10) -> float:
        """The whole run's expected cost: sources, writing, an audit, and each allowed review with its
        audit."""
        e = self.estimates
        return e.find + e.write(words) + e.audit + self.review_rounds * (e.review(sections) + e.audit)

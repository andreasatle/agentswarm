"""Jev's JSON wire format as Pydantic models. Ported from ~/Projects/Jev/jev_api.py."""

from pydantic import BaseModel

# --- Questions (what is sent) ---


class NoulCriteria(BaseModel):
    true: str  # what yes means
    false: str  # what no means


class NoulQuestion(BaseModel):
    type: str = "noul"
    instructions: str
    criteria: NoulCriteria | None = None


class ChoiceQuestion(BaseModel):
    type: str = "choice"
    instructions: str
    criteria: dict[str, str]  # option key -> description


class ScoreQuestion(BaseModel):
    type: str = "score"
    instructions: str
    criteria: list[str]  # ordered levels, lowest first


# --- Answers (what comes back) ---


class NoulAnswer(BaseModel):
    noul: float  # probability of yes; Noul has no confidence field


class ChoiceAnswer[E: str](BaseModel):
    choice: E
    confidence: float
    probabilities: dict[E, float]


class ScoreAnswer(BaseModel):
    score: float
    confidence: float
    probabilities: dict[int, float]  # level index -> probability


# --- Response envelope ---


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class JevResponse[A: BaseModel](BaseModel):
    model: str  # the concrete version that answered, e.g. "jev-1.13.0"
    answers: A
    usage: Usage

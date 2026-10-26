from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .scoring import DIMENSION_WEIGHTS


class DimensionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    score: int | None
    rationale: str
    limitations: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def known_dimension(cls, value: str) -> str:
        if value not in DIMENSION_WEIGHTS:
            raise ValueError("unknown rubric dimension")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def valid_score(cls, value: object) -> object:
        if value is not None and (type(value) is not int or not 1 <= value <= 5):
            raise ValueError("score must be an integer from 1 to 5 or null")
        return value


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp_start: float = Field(ge=0)
    timestamp_end: float = Field(ge=0)
    dimension: str
    type: Literal["strength", "improvement", "neutral"]
    observation: str
    evidence_refs: list[str]
    impact: str
    recommendation: str = ""
    confidence: Literal["low", "medium", "high"]


class Strength(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    explanation: str
    evidence_refs: list[str]


class Improvement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: Literal["high", "medium", "low"]
    title: str
    explanation: str
    recommendation: str
    evidence_refs: list[str]


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_status: Literal["completed", "insufficient_evidence"]
    overall_score: float | None
    rating: Literal["Poor", "Average", "Good", "Excellent"] | None
    evidence_coverage_percent: float = Field(ge=0, le=100)
    summary: str
    dimensions: list[DimensionResult]
    observations: list[Observation] = Field(default_factory=list)
    strengths: list[Strength] = Field(default_factory=list)
    priority_improvements: list[Improvement] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    dimension_percentages: dict[str, float | None] = Field(default_factory=dict)

    @field_validator("dimensions")
    @classmethod
    def exact_dimensions(cls, value: list[DimensionResult]) -> list[DimensionResult]:
        if [d.name for d in value] != list(DIMENSION_WEIGHTS):
            raise ValueError("dimensions must appear once each in rubric order")
        return value

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import DRIVER_PATTERN, MAX_SEED, STRATEGY_PATTERN, Mode

# ---------------------------------------------------------------- requests ----

SafetyCarLaps = Annotated[
    list[Annotated[int, Field(ge=1, le=99)]],
    Field(max_length=15, description="Laps forced to run under the safety car in every "
                                     "simulated race (a what-if), e.g. [30, 31, 32]"),
]


def _tidy_laps(laps: list[int]) -> list[int]:
    return sorted(set(laps))


class SimulationRequest(BaseModel):
    race_id: str
    driver: str = Field(pattern=DRIVER_PATTERN)
    strategy: str = Field(pattern=STRATEGY_PATTERN, examples=["M-24-H", "S-12-M-35-H"])
    mode: Mode = "pre_race"
    scenarios: int = Field(1000, ge=100, le=20000)
    seed: int | None = Field(None, ge=0, le=MAX_SEED,
                             description="Omit to have one generated; it is returned and stored.")
    safety_car_laps: SafetyCarLaps = []
    compare_with: str | None = Field(
        None, pattern=STRATEGY_PATTERN,
        description="A second strategy run on the same simulated races, for a paired comparison")

    @model_validator(mode="after")
    def tidy(self) -> SimulationRequest:
        self.safety_car_laps = _tidy_laps(self.safety_car_laps)
        if self.compare_with == self.strategy:
            raise ValueError("compare_with must differ from strategy")
        return self


class SearchSpace(BaseModel):
    one_stop: bool = True
    two_stop: bool = True
    min_stint_laps: int = Field(8, ge=3, le=40)
    lap_step: int = Field(2, ge=1, le=10)
    window_start: int = Field(0, ge=0, description="Earliest pit lap considered (0 = none)")
    window_end: int = Field(0, ge=0, description="Latest pit lap considered (0 = none)")
    max_candidates: int = Field(1000, ge=1)

    @model_validator(mode="after")
    def check(self) -> SearchSpace:
        if not (self.one_stop or self.two_stop):
            raise ValueError("enable one_stop, two_stop or both")
        if self.window_start and self.window_end and self.window_end < self.window_start:
            raise ValueError("window_end must be >= window_start")
        return self


class OptimizationRequest(BaseModel):
    race_id: str
    driver: str = Field(pattern=DRIVER_PATTERN)
    mode: Mode = "pre_race"
    scenarios: int = Field(500, ge=100, le=5000)
    seed: int | None = Field(None, ge=0, le=MAX_SEED)
    search: SearchSpace = SearchSpace()
    top_k: int = Field(10, ge=1, le=50)
    safety_car_laps: SafetyCarLaps = []
    compare_strategies: list[str] = Field(
        default_factory=list, max_length=5,
        description="Extra strategies ranked on the same scenarios, e.g. the one actually run")

    @model_validator(mode="after")
    def check_strategies(self) -> OptimizationRequest:
        import re
        for s in self.compare_strategies:
            if not re.match(STRATEGY_PATTERN, s):
                raise ValueError(f"compare_strategies: '{s}' must look like M-24-H or S-12-M-35-H")
        self.safety_car_laps = _tidy_laps(self.safety_car_laps)
        return self


class TraceRequest(BaseModel):
    race_id: str
    driver: str = Field(pattern=DRIVER_PATTERN)
    strategy: str = Field(pattern=STRATEGY_PATTERN)
    mode: Mode = "pre_race"
    seed: int = Field(ge=0, le=MAX_SEED)
    scenario: int | None = Field(
        None, ge=0, le=10**9,
        description="Which simulated race to replay. Omit for a typical one: a race where the "
                    "driver finishes at the median position of 200 simulated races.")
    safety_car_laps: SafetyCarLaps = []

    @model_validator(mode="after")
    def tidy(self) -> TraceRequest:
        self.safety_car_laps = _tidy_laps(self.safety_car_laps)
        return self


# --------------------------------------------------------------- responses ----


class JobLinks(BaseModel):
    self: str
    result: str


class JobAccepted(BaseModel):
    job_id: uuid.UUID
    kind: str
    status: str
    links: JobLinks


class JobError(BaseModel):
    kind: str
    message: str


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    race_id: str
    driver: str
    calibration_mode: Mode
    mode_label: str
    params: dict
    engine_version: str
    data_version: str
    error: JobError | None = None
    links: JobLinks


class Outcome(BaseModel):
    """'Does the driver finish?' and 'where, if they do?' kept separate."""
    finish_probability: float
    mean_position_if_finished: float
    p05_if_finished: int
    p50_if_finished: int
    p95_if_finished: int
    position_distribution_if_finished: list[float]  # Index 0 = P1.
    p_win: float
    p_podium: float
    p_points: float
    mean_scored_position: float  # DNF scored as field size + 1; used for ranking.


class Comparison(BaseModel):
    """Paired difference against the top-ranked candidate on matched scenarios
    (positive = worse, in expected scored position)."""
    diff_vs_best: float
    ci95: tuple[float, float]
    inconclusive: bool


class CandidateOut(BaseModel):
    rank: int
    strategy: str
    outcome: Outcome
    comparison_vs_best: Comparison
    notes: list[str]


class ResultMetadata(BaseModel):
    calibration_mode: Mode
    mode_label: str
    engine_version: str
    data_version: str
    seed: int
    scenarios: int
    calibration_id: int
    tires: list[dict]
    provenance: dict


class PairedComparison(BaseModel):
    """The simulated strategy against `strategy`, on the same races."""
    strategy: str
    outcome: Outcome
    notes: list[str]
    # Simulated strategy minus this one, in expected scored position
    # (negative = the simulated strategy finishes ahead on average).
    diff: float
    ci95: tuple[float, float]


class SimulationResult(BaseModel):
    kind: Literal["simulation"]
    job_id: uuid.UUID
    driver: str
    strategy: str
    outcome: Outcome
    notes: list[str]
    safety_car_laps: list[int] = []
    comparison: PairedComparison | None = None
    metadata: ResultMetadata


class OptimizationResult(BaseModel):
    kind: Literal["optimization"]
    job_id: uuid.UUID
    driver: str
    candidates_evaluated: int
    ranked: list[CandidateOut]
    compared_strategies: list[CandidateOut]
    # Every candidate as (strategy, mean scored position, finish probability),
    # for charting the pit window.
    landscape: list[tuple[str, float, float]] = []
    safety_car_laps: list[int] = []
    metadata: ResultMetadata


# ------------------------------------------------------------------ traces ----


class TracePit(BaseModel):
    lap: int
    compound: Literal["S", "M", "H"]


class TraceDriver(BaseModel):
    code: str
    grid: int
    strategy: str
    position: int
    finished: bool
    retired_lap: int | None
    positions: list[int | None]  # Index 0 = grid, then after each lap; None once retired.
    gaps: list[float | None]     # Seconds behind the leader, same indexing.
    pits: list[TracePit]


class TypicalInfo(BaseModel):
    sample_size: int
    median_position_if_finished: int


class TraceOut(BaseModel):
    race_id: str
    driver: str
    strategy: str
    seed: int
    scenario: int
    typical: TypicalInfo | None
    total_laps: int
    forced_safety_car_laps: list[int]
    safety_car_laps: list[int]
    virtual_safety_car_laps: list[int] = []
    drivers: list[TraceDriver]
    calibration_mode: Mode
    mode_label: str
    engine_version: str
    data_version: str

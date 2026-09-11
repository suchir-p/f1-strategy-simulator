from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import Mode


class ModeAvailability(BaseModel):
    available: bool
    label: str
    reason: str | None = None


class RaceSummary(BaseModel):
    id: str
    season: int
    round: int
    name: str
    total_laps: int
    tire_nomination: dict[str, str]
    note: str
    modes: dict[str, ModeAvailability]


class RaceDriverOut(BaseModel):
    code: str
    grid_position: int
    # Strategy the dashboard starts from: the rivals' reference strategy in
    # pre_race mode, the strategy actually run in race_fitted mode.
    default_strategy: str | None = None
    # Retrospective only (race_fitted): what really happened.
    actual_strategy: str | None = None
    actual_position: int | None = None
    actual_status: str | None = None


class TireOut(BaseModel):
    designation: str
    absolute_compound: str
    confidence: str
    clean_laps: int | None
    max_stint_laps: int
    pace_offset: float
    deg_linear: float
    deg_quadratic: float


class TiresOut(BaseModel):
    race_id: str
    mode: Mode
    mode_label: str
    data_version: str
    compounds: list[TireOut]
    provenance: dict

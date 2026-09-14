"""Validating and admitting jobs.

Everything that can be checked before running is checked here, so bad or
oversized requests fail fast with a 4xx instead of occupying a worker:
race and mode availability, driver, strategy validity (by the engine
itself), and the job size (candidates x scenarios).

Admission is serialized with a transaction-scoped PostgreSQL advisory lock, so
the pending-job limit holds even when many requests arrive at once.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import (Calibration, CalibrationMode, Job, JobKind, JobStatus, Race,
                                 RaceDriver)
from app.engine import engine
from app.schemas.common import MAX_SEED, MODE_LABELS
from app.schemas.jobs import OptimizationRequest, SimulationRequest

ADMISSION_LOCK_KEY = 7_340_001  # Arbitrary app-wide advisory lock id.


class QueueFull(Exception):
    pass


@dataclass
class Prepared:
    kind: JobKind
    race: Race
    calibration: Calibration
    params: dict


def unprocessable(msg: str) -> HTTPException:
    return HTTPException(422, detail=msg)


def current_calibration(s: Session, race: Race, mode: str, data_version: str) -> Calibration:
    cal = s.scalar(select(Calibration).where(
        Calibration.race_id == race.id, Calibration.mode == CalibrationMode(mode),
        Calibration.data_version == data_version))
    if cal is None:
        if mode == "pre_race":
            raise unprocessable(
                f"{race.name} has no pre-race calibration: too few earlier races in the season "
                "to estimate parameters without using this race. Use mode 'race_fitted' for a "
                "retrospective analysis.")
        raise unprocessable(f"no {mode} calibration for {race.name}")
    return cal


def _common(s: Session, req, data_version: str) -> tuple[Race, Calibration]:
    race = s.get(Race, req.race_id)
    if race is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown race '{req.race_id}'")
    if s.get(RaceDriver, (race.id, req.driver)) is None:
        raise unprocessable(f"driver {req.driver} did not take part in {race.name}")
    return race, current_calibration(s, race, req.mode, data_version)


def _check_strategy(cal: Calibration, strategy: str, field: str) -> None:
    error = engine().check_strategy(json.dumps(cal.config), strategy)
    if error:
        raise unprocessable(f"{field} '{strategy}': {error}")


def check_safety_car_laps(race: Race, laps: list[int]) -> None:
    late = [lap for lap in laps if lap > race.total_laps]
    if late:
        raise unprocessable(f"safety_car_laps: {race.name} has {race.total_laps} laps "
                            f"(got {', '.join(map(str, late))})")


def prepare_simulation(s: Session, req: SimulationRequest, settings: Settings,
                       data_version: str, test_action: str | None) -> Prepared:
    race, cal = _common(s, req, data_version)
    _check_strategy(cal, req.strategy, "strategy")
    if req.compare_with:
        _check_strategy(cal, req.compare_with, "compare_with")
    check_safety_car_laps(race, req.safety_car_laps)
    races = req.scenarios * (2 if req.compare_with else 1)
    if races > settings.max_races_per_job:
        raise unprocessable(f"job too large: {races} races > {settings.max_races_per_job}")
    params = {"race_id": race.id, "driver": req.driver, "mode": req.mode,
              "strategy": req.strategy, "scenarios": req.scenarios,
              "seed": req.seed if req.seed is not None else secrets.randbelow(MAX_SEED + 1),
              "seed_generated": req.seed is None,
              "safety_car_laps": req.safety_car_laps, "compare_with": req.compare_with}
    if test_action:
        params["test_action"] = test_action
    return Prepared(JobKind.simulation, race, cal, params)


def prepare_optimization(s: Session, req: OptimizationRequest, settings: Settings,
                         data_version: str, test_action: str | None) -> Prepared:
    race, cal = _common(s, req, data_version)
    for strat in req.compare_strategies:
        _check_strategy(cal, strat, "compare_strategies")
    check_safety_car_laps(race, req.safety_car_laps)
    sp = req.search
    if sp.max_candidates > settings.max_candidates:
        raise unprocessable(f"search.max_candidates may be at most {settings.max_candidates}")
    count = engine().count_candidates(
        json.dumps(cal.config), min_stint_laps=sp.min_stint_laps, lap_step=sp.lap_step,
        window_start=sp.window_start, window_end=sp.window_end, one_stop=sp.one_stop,
        two_stop=sp.two_stop, respect_stint_limits=True, limit=sp.max_candidates)
    if count > sp.max_candidates:
        raise unprocessable(
            f"the search space has more than {sp.max_candidates} candidates; increase lap_step, "
            "narrow the pit window, or raise search.max_candidates")
    if count == 0 and not req.compare_strategies:
        raise unprocessable("the search space contains no valid strategies")
    total = count + len(req.compare_strategies)
    if total * req.scenarios > settings.max_races_per_job:
        raise unprocessable(
            f"job too large: {total} candidates x {req.scenarios} scenarios = "
            f"{total * req.scenarios:,} races (limit {settings.max_races_per_job:,})")
    params = {"race_id": race.id, "driver": req.driver, "mode": req.mode,
              "scenarios": req.scenarios,
              "seed": req.seed if req.seed is not None else secrets.randbelow(MAX_SEED + 1),
              "seed_generated": req.seed is None,
              "search": sp.model_dump(), "candidates": total, "top_k": req.top_k,
              "compare_strategies": req.compare_strategies,
              "safety_car_laps": req.safety_car_laps}
    if test_action:
        params["test_action"] = test_action
    return Prepared(JobKind.optimization, race, cal, params)


def admit(s: Session, prepared: Prepared, settings: Settings, engine_version: str,
          data_version: str) -> Job:
    """Inserts the job if the pending queue has room; raises QueueFull if not.
    Must run inside the caller's transaction (committed by the caller)."""
    s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADMISSION_LOCK_KEY})
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.pending))
    if pending >= settings.max_pending_jobs:
        raise QueueFull()
    job = Job(kind=prepared.kind, status=JobStatus.pending, race_id=prepared.race.id,
              driver_code=prepared.params["driver"], calibration_id=prepared.calibration.id,
              calibration_mode=prepared.calibration.mode, params=prepared.params,
              engine_version=engine_version, data_version=data_version)
    s.add(job)
    s.flush()
    return job


def mode_label(mode: str) -> str:
    return MODE_LABELS[mode]

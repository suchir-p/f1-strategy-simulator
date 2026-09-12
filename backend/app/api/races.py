from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AppState, session, state
from app.database.models import Calibration, CalibrationMode, Race
from app.schemas.common import MODE_LABELS, Mode
from app.schemas.races import ModeAvailability, RaceDriverOut, RaceSummary, TireOut, TiresOut

router = APIRouter(prefix="/races", tags=["races"])

NO_PRE_RACE = ("too few earlier races in the season to estimate parameters without using "
               "this race")


def _get_race(s: Session, race_id: str) -> Race:
    race = s.get(Race, race_id)
    if race is None:
        raise HTTPException(404, detail=f"unknown race '{race_id}'")
    return race


@router.get("", response_model=list[RaceSummary])
def list_races(s: Session = Depends(session), st: AppState = Depends(state)):
    races = s.scalars(select(Race).order_by(Race.season, Race.round)).all()
    available = {(c.race_id, c.mode.value) for c in s.scalars(
        select(Calibration).where(Calibration.data_version == st.data_version))}
    out = []
    for r in races:
        modes = {}
        for m in ("pre_race", "race_fitted"):
            ok = (r.id, m) in available
            modes[m] = ModeAvailability(available=ok, label=MODE_LABELS[m],
                                        reason=None if ok else (NO_PRE_RACE if m == "pre_race"
                                                                else "not calibrated"))
        out.append(RaceSummary(id=r.id, season=r.season, round=r.round, name=r.name,
                               total_laps=r.total_laps, tire_nomination=r.tire_nomination,
                               note=r.note, modes=modes))
    return out


def _label(strategy: dict) -> str:
    return strategy["start"] + "".join(f"-{x['lap']}-{x['compound']}" for x in strategy["stops"])


@router.get("/{race_id}/drivers", response_model=list[RaceDriverOut])
def race_drivers(race_id: str, mode: Mode = Query("pre_race"), s: Session = Depends(session),
                 st: AppState = Depends(state)):
    """Drivers in grid order. Race outcomes (actual strategy and result) are
    only included in race_fitted mode, so a pre-race view never shows them."""
    race = _get_race(s, race_id)
    cal = s.scalar(select(Calibration).where(
        Calibration.race_id == race.id, Calibration.mode == CalibrationMode(mode),
        Calibration.data_version == st.data_version))
    strategies = {d["code"]: _label(d["strategy"]) for d in cal.config["drivers"]} if cal else {}
    results = {r["driver"]: r for r in race.actual_result}
    out = []
    for d in race.drivers:
        row = RaceDriverOut(code=d.driver_code, grid_position=d.grid_position,
                            default_strategy=strategies.get(d.driver_code))
        if mode == "race_fitted":
            res = results.get(d.driver_code, {})
            row.actual_strategy = strategies.get(d.driver_code)
            row.actual_position = res.get("position")
            row.actual_status = res.get("status")
        out.append(row)
    return out


@router.get("/{race_id}/tires", response_model=TiresOut)
def race_tires(race_id: str, mode: Mode = Query("pre_race"), s: Session = Depends(session),
               st: AppState = Depends(state)):
    race = _get_race(s, race_id)
    cal = s.scalar(select(Calibration).where(
        Calibration.race_id == race.id, Calibration.mode == CalibrationMode(mode),
        Calibration.data_version == st.data_version))
    if cal is None:
        raise HTTPException(404, detail=f"{race.name} has no {mode} calibration"
                            + (f": {NO_PRE_RACE}" if mode == "pre_race" else ""))
    return TiresOut(
        race_id=race.id, mode=mode, mode_label=MODE_LABELS[mode], data_version=cal.data_version,
        compounds=[TireOut.model_validate(t, from_attributes=True) for t in cal.tires],
        provenance=cal.provenance)

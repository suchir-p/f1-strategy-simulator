"""Loads the calibration bundle (data/bundle/<season>_bundle.json) into PostgreSQL.

Idempotent: races and drivers are upserted, and calibrations are inserted
once per data_version. Calibrations from older data versions are kept, so
past jobs still point at the exact config they ran with.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import (Calibration, CalibrationMode, Driver, Race, RaceDriver,
                                 TireCalibration)


def load_bundle(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def seed(session: Session, bundle: dict) -> int:
    """Returns the number of calibrations inserted."""
    version = bundle["data_version"]
    inserted = 0
    known_drivers = set(session.scalars(select(Driver.code)))
    for r in bundle["races"]:
        race = session.get(Race, r["id"]) or Race(id=r["id"])
        race.season, race.round, race.name = r["season"], r["round"], r["name"]
        race.total_laps, race.tire_nomination = r["total_laps"], r["tire_nomination"]
        race.note, race.actual_result = r.get("note", ""), r["actual_result"]
        session.add(race)
        session.flush()

        existing = {rd.driver_code: rd for rd in session.scalars(
            select(RaceDriver).where(RaceDriver.race_id == r["id"]))}
        for d in r["drivers"]:
            if d["code"] not in known_drivers:
                session.add(Driver(code=d["code"]))
                known_drivers.add(d["code"])
                session.flush()
            rd = existing.get(d["code"]) or RaceDriver(race_id=r["id"], driver_code=d["code"])
            rd.grid_position = d["grid_position"]
            session.add(rd)

        for mode, cal in r["calibrations"].items():
            exists = session.scalar(select(Calibration.id).where(
                Calibration.race_id == r["id"], Calibration.mode == CalibrationMode(mode),
                Calibration.data_version == version))
            if exists:
                continue
            c = Calibration(race_id=r["id"], mode=CalibrationMode(mode), data_version=version,
                            config=cal["config"], provenance=cal["provenance"])
            for t in cal["tires"]:
                c.tires.append(TireCalibration(**t))
            session.add(c)
            inserted += 1
    session.flush()
    return inserted

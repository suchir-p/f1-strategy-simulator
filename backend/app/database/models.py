"""PostgreSQL schema.

Relational tables hold what the API filters and joins on (races, drivers,
calibrations, tire parameters, jobs). Engine inputs and outputs that are only
ever read whole (race configs, provenance, job parameters and results) are
JSONB. No per-lap or per-scenario rows: only aggregated results are stored.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (JSON, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class CalibrationMode(str, enum.Enum):
    pre_race = "pre_race"        # Only information available before the race.
    race_fitted = "race_fitted"  # Fitted on the race itself: retrospective.


class JobKind(str, enum.Enum):
    simulation = "simulation"
    optimization = "optimization"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Race(Base):
    __tablename__ = "races"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "2024-italy"
    season: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    total_laps: Mapped[int] = mapped_column(Integer)
    tire_nomination: Mapped[dict] = mapped_column(JsonType)
    note: Mapped[str] = mapped_column(Text, default="")
    actual_result: Mapped[list] = mapped_column(JsonType)

    drivers: Mapped[list[RaceDriver]] = relationship(back_populates="race",
                                                     order_by="RaceDriver.grid_position")
    calibrations: Mapped[list[Calibration]] = relationship(back_populates="race")


class Driver(Base):
    __tablename__ = "drivers"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)


class RaceDriver(Base):
    __tablename__ = "race_drivers"

    race_id: Mapped[str] = mapped_column(ForeignKey("races.id"), primary_key=True)
    driver_code: Mapped[str] = mapped_column(ForeignKey("drivers.code"), primary_key=True)
    grid_position: Mapped[int] = mapped_column(Integer)

    race: Mapped[Race] = relationship(back_populates="drivers")


class Calibration(Base):
    """One engine-ready race config for a (race, mode, data version)."""
    __tablename__ = "calibrations"
    __table_args__ = (UniqueConstraint("race_id", "mode", "data_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(ForeignKey("races.id"))
    mode: Mapped[CalibrationMode] = mapped_column(Enum(CalibrationMode, name="calibration_mode"))
    data_version: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JsonType)
    provenance: Mapped[dict] = mapped_column(JsonType)

    race: Mapped[Race] = relationship(back_populates="calibrations")
    tires: Mapped[list[TireCalibration]] = relationship(back_populates="calibration",
                                                        order_by="TireCalibration.id")


class TireCalibration(Base):
    __tablename__ = "tire_calibrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calibration_id: Mapped[int] = mapped_column(ForeignKey("calibrations.id"))
    designation: Mapped[str] = mapped_column(String(8))         # soft / medium / hard
    absolute_compound: Mapped[str] = mapped_column(String(4))   # C1..C6
    confidence: Mapped[str] = mapped_column(String(16))         # race / race_low / pooled / fallback
    clean_laps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_stint_laps: Mapped[int] = mapped_column(Integer)
    pace_offset: Mapped[float] = mapped_column(Float)
    deg_linear: Mapped[float] = mapped_column(Float)
    deg_quadratic: Mapped[float] = mapped_column(Float)

    calibration: Mapped[Calibration] = relationship(back_populates="tires")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[JobKind] = mapped_column(Enum(JobKind, name="job_kind"))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"),
                                              default=JobStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    race_id: Mapped[str] = mapped_column(ForeignKey("races.id"))
    driver_code: Mapped[str] = mapped_column(String(3))
    calibration_id: Mapped[int] = mapped_column(ForeignKey("calibrations.id"))
    calibration_mode: Mapped[CalibrationMode] = mapped_column(
        Enum(CalibrationMode, name="calibration_mode"))
    # The complete effective configuration, including the seed actually used.
    params: Mapped[dict] = mapped_column(JsonType)
    engine_version: Mapped[str] = mapped_column(String(32))
    data_version: Mapped[str] = mapped_column(String(64))

    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)

    calibration: Mapped[Calibration] = relationship()

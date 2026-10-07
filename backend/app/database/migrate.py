"""Applies database migrations (Alembic) at startup.

Every API process calls migrate() before serving. A PostgreSQL advisory lock
makes concurrent starts (several replicas, or the API and a test) take turns.
Databases created before migrations existed, by SQLAlchemy's create_all, have
exactly the schema of the first revision; they are stamped at it and then
upgraded like any other.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION_LOCK_KEY = 7_340_002
BASELINE = "0001"


def alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def migrate(engine: Engine) -> str:
    """Upgrades the database to the latest revision; returns what it did."""
    cfg = alembic_config()
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        cfg.attributes["connection"] = conn
        insp = inspect(conn)
        adopted = not insp.has_table("alembic_version") and insp.has_table("races")
        if adopted:
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return f"schema at {version}" + (" (adopted a pre-migration database)" if adopted else "")

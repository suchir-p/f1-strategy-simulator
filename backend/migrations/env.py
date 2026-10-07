"""Alembic environment: the database URL comes from the app's settings (or a
connection passed in by app/database/migrate.py), never from alembic.ini."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app.database.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    from app.config import get_settings
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) migrations are not supported; run against a database")
run_migrations_online()

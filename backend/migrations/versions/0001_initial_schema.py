"""Initial schema: races, drivers, calibrations, tires, jobs

Generated from app/database/models.py. Databases created before migrations
existed (by SQLAlchemy create_all) have exactly this schema and are stamped
at this revision on first startup instead of being recreated.

Revision ID: 0001
Revises:
Create Date: 2026-10-15 01:53:59.668625
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('drivers',
    sa.Column('code', sa.String(length=3), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('races',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('total_laps', sa.Integer(), nullable=False),
    sa.Column('tire_nomination', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('actual_result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('calibrations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('race_id', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.Enum('pre_race', 'race_fitted', name='calibration_mode'), nullable=False),
    sa.Column('data_version', sa.String(length=64), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('provenance', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['race_id'], ['races.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('race_id', 'mode', 'data_version')
    )
    op.create_table('race_drivers',
    sa.Column('race_id', sa.String(length=64), nullable=False),
    sa.Column('driver_code', sa.String(length=3), nullable=False),
    sa.Column('grid_position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['driver_code'], ['drivers.code'], ),
    sa.ForeignKeyConstraint(['race_id'], ['races.id'], ),
    sa.PrimaryKeyConstraint('race_id', 'driver_code')
    )
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('simulation', 'optimization', name='job_kind'), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', name='job_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('race_id', sa.String(length=64), nullable=False),
    sa.Column('driver_code', sa.String(length=3), nullable=False),
    sa.Column('calibration_id', sa.Integer(), nullable=False),
    sa.Column('calibration_mode', sa.Enum('pre_race', 'race_fitted', name='calibration_mode'), nullable=False),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('data_version', sa.String(length=64), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('error_kind', sa.String(length=32), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['calibration_id'], ['calibrations.id'], ),
    sa.ForeignKeyConstraint(['race_id'], ['races.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_jobs_status_created', 'jobs', ['status', 'created_at'], unique=False)
    op.create_table('tire_calibrations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('calibration_id', sa.Integer(), nullable=False),
    sa.Column('designation', sa.String(length=8), nullable=False),
    sa.Column('absolute_compound', sa.String(length=4), nullable=False),
    sa.Column('confidence', sa.String(length=16), nullable=False),
    sa.Column('clean_laps', sa.Integer(), nullable=True),
    sa.Column('max_stint_laps', sa.Integer(), nullable=False),
    sa.Column('pace_offset', sa.Float(), nullable=False),
    sa.Column('deg_linear', sa.Float(), nullable=False),
    sa.Column('deg_quadratic', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['calibration_id'], ['calibrations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('tire_calibrations')
    op.drop_index('ix_jobs_status_created', table_name='jobs')
    op.drop_table('jobs')
    op.drop_table('race_drivers')
    op.drop_table('calibrations')
    op.drop_table('races')
    op.drop_table('drivers')
    for enum in ("job_status", "job_kind", "calibration_mode"):
        op.execute(f"DROP TYPE IF EXISTS {enum}")

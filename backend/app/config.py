"""Application settings, read from environment variables prefixed F1SIM_."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
TRACE_RATE_MULTIPLIER = 6
WEAK_PASSWORDS = {"", "f1sim", "postgres", "password", "change-me-to-a-long-random-string"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="F1SIM_", env_file=".env", extra="ignore",
                                      env_ignore_empty=True)

    # development: local defaults. production: for anything other people can
    # reach; refuses default credentials and turns on the limits below.
    env: Literal["development", "production"] = "development"

    database_url: str = "postgresql+psycopg://f1sim:f1sim@localhost:5432/f1sim"
    # Directory containing the compiled f1sim Python module.
    engine_path: Path = ROOT / "engine" / "build"
    bundle_path: Path = ROOT / "data" / "bundle" / "2024_bundle.json"

    # ---- execution limits -------------------------------------------------
    # Worker processes run engine jobs; each uses this many OpenMP threads.
    # workers x omp_threads_per_worker should not exceed the CPU core count.
    max_workers: int = Field(1, ge=1)
    omp_threads_per_worker: int = Field(2, ge=1)
    # Jobs accepted but not yet running. Beyond this, submissions get HTTP 429.
    max_pending_jobs: int = Field(20, ge=1)
    # Largest job accepted: candidates x scenarios (races simulated).
    max_races_per_job: int = Field(1_000_000, ge=1)
    # Cap on grid-search candidates per optimization request.
    max_candidates: int = Field(3000, ge=1)
    # Race traces (one replayed race, ~200 sampled to find a typical one) are
    # small enough to run inline in the API process, single-threaded. This
    # bounds how many run at once; beyond it, requests get HTTP 429.
    max_concurrent_traces: int = Field(2, ge=1)
    dispatcher_poll_seconds: float = 0.5
    dispatcher_enabled: bool = True

    # ---- public-facing safety (see docs/deployment.md) -------------------------
    # Simulation and search submissions per client per minute; race replays
    # (sent while dragging a pit marker) get TRACE_RATE_MULTIPLIER times as many.
    # None = 0 (off) in development, 20 in production.
    rate_limit_per_minute: int | None = Field(None, ge=0)
    # Identify clients by the first X-Forwarded-For address. Only enable when
    # the API is reachable solely through a proxy that sets it (docker compose
    # does: the web service forwards /api/*); otherwise clients can spoof it.
    trust_forwarded_for: bool = False
    # GET /jobs lists everyone's jobs. Jobs are otherwise reachable only by
    # their random id. None = shown in development, hidden in production.
    expose_job_list: bool | None = None

    # Enables the X-Test-Action header (crash, raise, sleep) for integration
    # tests. Never enable in a real deployment.
    test_hooks: bool = False

    @model_validator(mode="after")
    def production_rules(self) -> Settings:
        prod = self.env == "production"
        if self.rate_limit_per_minute is None:
            self.rate_limit_per_minute = 20 if prod else 0
        if self.expose_job_list is None:
            self.expose_job_list = not prod
        if prod:
            if self.test_hooks:
                raise ValueError("F1SIM_TEST_HOOKS must be off in production")
            password = make_url(self.database_url).password or ""
            if password.lower() in WEAK_PASSWORDS or len(password) < 12:
                raise ValueError(
                    "refusing to start in production with a default or short database password; "
                    "set POSTGRES_PASSWORD (docker compose) or F1SIM_DATABASE_URL to a password "
                    "of at least 12 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

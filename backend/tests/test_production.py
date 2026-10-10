"""Production mode: refuses unsafe configuration, hides the job list, and
rate-limits the endpoints that start engine work."""

from __future__ import annotations

import secrets
from contextlib import contextmanager

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.config import Settings
from app.services.ratelimit import RateLimiter
from conftest import PG, SIM, _admin, run_server

STRONG = secrets.token_urlsafe(18)


@contextmanager
def production_server(database_factory, **overrides):
    """A server in production mode, on a database owned by a role with a strong
    password (production refuses the development credentials)."""
    role = f"prod_{secrets.token_hex(4)}"
    with _admin().connect() as c:
        c.execute(text(f"CREATE ROLE {role} LOGIN SUPERUSER PASSWORD '{STRONG}'"))
    url = database_factory()
    host = PG.split("@", 1)[1]
    prod_url = f"postgresql+psycopg://{role}:{STRONG}@{host}/{url.rsplit('/', 1)[1]}"
    try:
        with run_server(prod_url, env="production", test_hooks=False, **overrides) as srv:
            yield srv
    finally:
        name = url.rsplit("/", 1)[1]
        with _admin().connect() as c:  # The role owns objects in it: drop the database first.
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            c.execute(text(f"DROP ROLE {role}"))


def test_production_refuses_default_or_weak_credentials():
    for url in ("postgresql+psycopg://f1sim:f1sim@db:5432/f1sim",
                "postgresql+psycopg://f1sim:short@db:5432/f1sim",
                "postgresql+psycopg://f1sim@db:5432/f1sim"):
        with pytest.raises(ValidationError, match="database password"):
            Settings(env="production", database_url=url)
    with pytest.raises(ValidationError, match="TEST_HOOKS"):
        Settings(env="production", test_hooks=True,
                 database_url=f"postgresql+psycopg://f1sim:{STRONG}@db/f1sim")
    ok = Settings(env="production", database_url=f"postgresql+psycopg://f1sim:{STRONG}@db/f1sim")
    assert ok.rate_limit_per_minute == 20 and ok.expose_job_list is False
    dev = Settings()
    assert dev.rate_limit_per_minute == 0 and dev.expose_job_list is True


def test_rate_limiter_window():
    rl = RateLimiter()
    assert all(rl.hit("a", "jobs", 3, now=0.0) is None for _ in range(3))
    wait = rl.hit("a", "jobs", 3, now=10.0)
    assert wait == pytest.approx(50.0)
    assert rl.hit("b", "jobs", 3, now=10.0) is None       # Per client...
    assert rl.hit("a", "traces", 3, now=10.0) is None     # ...and per bucket.
    assert rl.hit("a", "jobs", 3, now=61.0) is None       # New window.


def test_production_server_limits_and_hides_jobs(database_factory):
    with production_server(database_factory, rate_limit_per_minute=2,
                           trust_forwarded_for=True) as srv:
        post = srv.client.post
        a = {"x-forwarded-for": "203.0.113.7, 10.0.0.2"}
        b = {"x-forwarded-for": "198.51.100.9"}
        assert post("/simulations", json=SIM, headers=a).status_code == 202
        assert post("/simulations", json=SIM, headers=a).status_code == 202
        r = post("/simulations", json=SIM, headers=a)
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        assert post("/optimizations", json={"race_id": "2024-italy", "driver": "LEC"},
                    headers=a).status_code == 429  # Same "jobs" budget.
        assert post("/simulations", json=SIM, headers=b).status_code == 202  # Another client.
        # Job ids still work for whoever has them; listing everyone's does not.
        job_id = post("/simulations", json=SIM, headers=b).json()["job_id"]
        assert srv.client.get(f"/jobs/{job_id}").status_code == 200
        assert srv.client.get("/jobs").status_code == 404
        # Test hooks are off: the header is ignored.
        r = post("/simulations", json={**SIM, "seed": 1},
                 headers={"x-test-action": "crash", "x-forwarded-for": "192.0.2.1"})
        assert srv.wait(r.json()["job_id"])["status"] == "completed"

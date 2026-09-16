"""Integration-test fixtures.

Every server fixture gets a fresh PostgreSQL database and runs the real app
(uvicorn in a background thread, real worker processes, real engine), so the
tests exercise the same paths as production.

Requires a PostgreSQL server: set F1SIM_TEST_PG to its URL without a database
name (default postgresql+psycopg://f1sim:f1sim@localhost:5432, the
docker-compose database). The user needs permission to create databases.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

PG = os.environ.get("F1SIM_TEST_PG", "postgresql+psycopg://f1sim:f1sim@localhost:5432")


def _admin():
    return create_engine(f"{PG}/postgres", isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def database_factory():
    created = []

    def make() -> str:
        name = f"f1sim_test_{uuid.uuid4().hex[:8]}"
        with _admin().connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return f"{PG}/{name}"

    yield make
    eng = _admin()
    with eng.connect() as c:
        for name in created:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    eng.dispose()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunningServer:
    def __init__(self, app, client: httpx.Client) -> None:
        self.app = app
        self.client = client

    @property
    def state(self):
        return self.app.state.f1sim

    def wait(self, job_id: str, timeout: float = 60, until=("completed", "failed")) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.client.get(f"/jobs/{job_id}").json()
            if job["status"] in until:
                return job
            time.sleep(0.1)
        raise TimeoutError(f"job {job_id} still {job['status']}")


@contextmanager
def run_server(database_url: str, **overrides) -> Iterator[RunningServer]:
    options = {"test_hooks": True, "dispatcher_poll_seconds": 0.1, **overrides}
    settings = Settings(database_url=database_url, **options)
    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("test server failed to start")
        time.sleep(0.05)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        try:
            yield RunningServer(app, client)
        finally:
            server.should_exit = True
            thread.join(timeout=30)


@pytest.fixture(scope="module")
def server(database_factory) -> Iterator[RunningServer]:
    """Shared server for read-mostly tests: 1 worker, 2 OpenMP threads."""
    with run_server(database_factory()) as srv:
        yield srv


@pytest.fixture
def fresh_server(database_factory):
    """Factory for tests that need their own database and settings."""
    @contextmanager
    def make(**overrides):
        with run_server(database_factory(), **overrides) as srv:
            yield srv
    return make


SIM = {"race_id": "2024-italy", "driver": "LEC", "strategy": "M-15-H", "scenarios": 200}

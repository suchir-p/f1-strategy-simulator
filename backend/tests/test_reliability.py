"""Execution under load and failure: admission control, isolation of
concurrent jobs, native errors, worker crashes, restarts, and calibration
integrity of what is stored."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.models import Calibration, CalibrationMode, Race
from app.engine import engine
from conftest import SIM, run_server
from test_api import submit


def test_pending_limit_holds_under_concurrent_submissions(fresh_server):
    with fresh_server(max_pending_jobs=3) as srv:
        srv.state.dispatcher.pause()  # Nothing leaves the queue during the burst.
        bodies = [{**SIM, "seed": i} for i in range(12)]
        with ThreadPoolExecutor(12) as pool:
            codes = list(pool.map(lambda b: srv.client.post("/simulations", json=b), bodies))
        statuses = sorted(r.status_code for r in codes)
        assert statuses.count(202) == 3 and statuses.count(429) == 9
        rejected = next(r for r in codes if r.status_code == 429)
        assert rejected.headers["retry-after"] == "10"
        assert srv.client.get("/health").json()["pending"] == 3
        srv.state.dispatcher.resume()
        for r in codes:
            if r.status_code == 202:
                assert srv.wait(r.json()["job_id"])["status"] == "completed"


def test_concurrent_jobs_are_isolated(fresh_server):
    """Jobs running at the same time in different workers each get exactly the
    result the engine gives for their own inputs."""
    requests = [
        {"race_id": "2024-italy", "driver": "LEC", "strategy": "M-15-H", "seed": 11},
        {"race_id": "2024-italy", "driver": "NOR", "strategy": "M-20-H", "seed": 12},
        {"race_id": "2024-hungary", "driver": "PIA", "strategy": "M-18-H-47-M", "seed": 13},
        {"race_id": "2024-hungary", "driver": "VER", "strategy": "M-21-H-49-M", "seed": 14},
        {"race_id": "2024-spain", "driver": "NOR", "strategy": "S-23-M-47-S", "seed": 15},
        {"race_id": "2024-spain", "driver": "HAM", "strategy": "S-16-M-43-S", "seed": 16},
    ]
    with fresh_server(max_workers=2, omp_threads_per_worker=1) as srv:
        with ThreadPoolExecutor(6) as pool:
            ids = list(pool.map(
                lambda b: submit(srv, "/simulations", {**b, "scenarios": 300}), requests))
        for i in ids:
            assert srv.wait(i)["status"] == "completed"
        results = [srv.client.get(f"/jobs/{i}/result").json() for i in ids]
        with Session(create_engine(srv.state.settings.database_url)) as s:
            configs = {c.race_id: c.config for c in s.scalars(select(Calibration).where(
                Calibration.mode == CalibrationMode.pre_race))}
        for req, res in zip(requests, results):
            direct = json.loads(engine().simulate(json.dumps(configs[req["race_id"]]), req["driver"],
                                                  req["strategy"], scenarios=300, seed=req["seed"],
                                                  threads=2))
            assert res["outcome"]["mean_scored_position"] == direct["stats"]["mean_position"]
            assert res["outcome"]["finish_probability"] == direct["stats"]["finish_probability"]


def test_native_exception_fails_job_cleanly(server):
    job_id = submit(server, "/simulations", SIM, **{"X-Test-Action": "native_error"})
    job = server.wait(job_id)
    assert job["status"] == "failed"
    assert job["error"]["kind"] == "engine_error"
    assert job_id in job["error"]["message"] and "Traceback" not in job["error"]["message"]
    assert server.client.get(f"/jobs/{job_id}/result").status_code == 409
    # The worker is still usable afterwards.
    assert server.wait(submit(server, "/simulations", SIM))["status"] == "completed"


def test_internal_details_are_not_leaked(server):
    job = server.wait(submit(server, "/simulations", SIM, **{"X-Test-Action": "raise"}))
    assert job["status"] == "failed" and "/secret/path" not in job["error"]["message"]


def test_worker_crash_is_contained(server):
    job = server.wait(submit(server, "/simulations", SIM, **{"X-Test-Action": "crash"}))
    assert job["status"] == "failed" and job["error"]["kind"] == "worker_crashed"
    # A fresh pool takes over.
    assert server.wait(submit(server, "/simulations", SIM))["status"] == "completed"


def test_restart_fails_running_jobs_and_keeps_pending_ones(database_factory):
    url = database_factory()
    with run_server(url) as first:
        running = submit(first, "/simulations", SIM, **{"X-Test-Action": "sleep:30"})
        first.wait(running, until=("running",))
        first.state.dispatcher.pause()
        queued = submit(first, "/simulations", SIM)
    # The server stopped with one job mid-run and one waiting.
    with run_server(url) as second:
        job = second.client.get(f"/jobs/{running}").json()
        assert job["status"] == "failed" and job["error"]["kind"] == "interrupted"
        assert second.wait(queued)["status"] == "completed"


def test_stored_pre_race_calibrations_use_only_earlier_races(server):
    with Session(create_engine(server.state.settings.database_url)) as s:
        rounds = {r.id: r.round for r in s.scalars(select(Race))}
        pre = s.scalars(select(Calibration).where(Calibration.mode == CalibrationMode.pre_race)).all()
        assert len(pre) == 15
        for cal in pre:
            trained = cal.provenance["trained_on"]
            assert trained and all(rounds[t] < rounds[cal.race_id] for t in trained)
            assert {d["strategy_source"] for d in cal.config["drivers"]} == {"reference"}
            assert "safety_car.lap_time" in cal.provenance["event_measured_track_constants"]


# ---------------------------------------------------------------- migrations ----

def test_migrations_match_the_models(database_factory):
    """The migrations produce exactly the schema the models describe."""
    from alembic import command
    from app.database.migrate import alembic_config, migrate

    url = database_factory()
    engine = create_engine(url)
    migrate(engine)
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.check(cfg)  # Raises if autogenerate would emit any change.
    engine.dispose()


def test_pre_migration_database_is_adopted(database_factory):
    """A database created by create_all (before migrations existed) is stamped
    at the baseline and keeps its data."""
    from sqlalchemy import text

    from app.database.migrate import migrate
    from app.database.models import Base, Driver

    url = database_factory()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Driver(code="ZZZ"))
        s.commit()
    assert "adopted" in migrate(engine)
    assert "adopted" not in migrate(engine)  # Second start: nothing to adopt.
    with engine.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        assert c.execute(text("SELECT count(*) FROM drivers WHERE code = 'ZZZ'")).scalar() == 1
    engine.dispose()

"""Runs pending jobs on a bounded pool of worker processes.

PostgreSQL is the queue. A background thread claims the oldest pending job
with `FOR UPDATE SKIP LOCKED` whenever a worker slot is free, runs it on a
`spawn` process pool, and records the result or a sanitized failure.

Guarantees and non-guarantees:
  * At most `max_workers` jobs run at once; admission control (see
    services/admission.py) separately bounds how many may wait.
  * Pending jobs survive an API restart (they are rows, not in-memory items).
  * A job that was running when the process stopped is marked failed
    ("interrupted") at the next startup. There is no automatic retry and no
    exactly-once execution.
  * If a worker process dies, Python's ProcessPoolExecutor fails every job
    running in that pool at the time; those jobs are marked "worker_crashed"
    and a fresh pool is started.
  * Designed for a single API instance.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import uuid
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from sqlalchemy import text

from app.config import Settings
from app.database.models import Job, JobStatus
from app.database.session import Database
from app.workers.runner import init_worker, run_job

log = logging.getLogger("f1sim.dispatcher")

CLAIM_SQL = text("""
    UPDATE jobs SET status = 'running', started_at = now()
    WHERE id = (SELECT id FROM jobs WHERE status = 'pending'
                ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
    RETURNING id
""")


def sanitize(message: str, limit: int = 300) -> str:
    one_line = " ".join(str(message).split())
    return one_line[:limit]


def recover_interrupted(db: Database) -> int:
    """Marks jobs left 'running' by a previous process as failed."""
    with db.session() as s:
        res = s.execute(text("""
            UPDATE jobs SET status = 'failed', finished_at = now(), error_kind = 'interrupted',
                   error_message = 'The server stopped while this job was running. Submit it again.'
            WHERE status = 'running'
        """))
        return res.rowcount or 0


class Dispatcher:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._lock = threading.Lock()
        self._inflight: dict[Future, uuid.UUID] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._pool: ProcessPoolExecutor | None = None
        self._pool_broken = False
        self._thread: threading.Thread | None = None

    # ---- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._pool = self._new_pool()
        self._thread = threading.Thread(target=self._run, name="f1sim-dispatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops claiming jobs and terminates workers. Jobs still running stay
        'running' in the database and are failed by the next startup."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
        if self._pool:
            self._terminate(self._pool)

    def notify(self) -> None:
        self._wake.set()

    def pause(self) -> None:  # Used by tests to hold jobs in the queue.
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    @property
    def running(self) -> int:
        with self._lock:
            return len(self._inflight)

    # ---- internals -----------------------------------------------------------

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self.settings.max_workers,
            mp_context=mp.get_context("spawn"),
            initializer=init_worker,
            initargs=(str(self.settings.engine_path), self.settings.omp_threads_per_worker),
        )

    @staticmethod
    def _terminate(pool: ProcessPoolExecutor) -> None:
        # Grab the worker processes first: shutdown() clears the executor's map.
        procs = list((getattr(pool, "_processes", None) or {}).values())
        pool.shutdown(wait=False, cancel_futures=True)
        for proc in procs:
            if proc.is_alive():
                proc.terminate()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._pool_broken:
                    self._replace_pool()
                if not self._paused.is_set():
                    self._fill_slots()
            except Exception:  # Never let the loop die; retry after the poll interval.
                log.exception("dispatcher loop error")
            self._wake.wait(self.settings.dispatcher_poll_seconds)
            self._wake.clear()

    def _replace_pool(self) -> None:
        log.warning("worker pool broken; starting a new one")
        old, self._pool = self._pool, self._new_pool()
        self._pool_broken = False
        if old:
            self._terminate(old)

    def _fill_slots(self) -> None:
        while self.running < self.settings.max_workers and not self._stop.is_set():
            claimed = self._claim()
            if claimed is None:
                return
            job_id, payload = claimed
            try:
                fut = self._pool.submit(run_job, payload)
            except BrokenProcessPool:
                self._replace_pool()
                fut = self._pool.submit(run_job, payload)
            with self._lock:
                self._inflight[fut] = job_id
            fut.add_done_callback(self._on_done)

    def _claim(self) -> tuple[uuid.UUID, dict] | None:
        with self.db.session() as s:
            job_id = s.execute(CLAIM_SQL).scalar()
            if job_id is None:
                return None
            job = s.get(Job, job_id)
            payload = {
                "kind": job.kind.value,
                "params": job.params,
                "race_config": job.calibration.config,
                "threads": self.settings.omp_threads_per_worker,
                "test_action": job.params.get("test_action") if self.settings.test_hooks else None,
            }
            return job_id, payload

    def _on_done(self, fut: Future) -> None:
        with self._lock:
            job_id = self._inflight.pop(fut, None)
        if job_id is None or self._stop.is_set():
            return
        try:
            result = fut.result()
        except CancelledError:
            return
        except BrokenProcessPool:
            self._pool_broken = True
            self._finish(job_id, error=("worker_crashed",
                                        "The simulation worker process stopped unexpectedly."))
        except ValueError as exc:  # Bad input detected by the engine: safe, our own message.
            self._finish(job_id, error=("invalid_input", sanitize(exc)))
        except Exception:
            log.exception("job %s failed", job_id)
            self._finish(job_id, error=("engine_error",
                                        f"The simulation engine failed (job {job_id}); "
                                        "details are in the server log."))
        else:
            self._finish(job_id, result=result)
        self._wake.set()

    def _finish(self, job_id: uuid.UUID, result: dict | None = None,
                error: tuple[str, str] | None = None) -> None:
        try:
            with self.db.session() as s:
                if error is None:
                    s.execute(text("""UPDATE jobs SET status = 'completed', finished_at = now(),
                                             result = CAST(:result AS JSONB)
                                      WHERE id = :id AND status = 'running'"""),
                              {"id": job_id, "result": _json(result)})
                else:
                    s.execute(text("""UPDATE jobs SET status = 'failed', finished_at = now(),
                                             error_kind = :kind, error_message = :msg
                                      WHERE id = :id AND status = 'running'"""),
                              {"id": job_id, "kind": error[0], "msg": error[1]})
        except Exception:
            log.exception("could not record the outcome of job %s", job_id)


def _json(value: dict) -> str:
    import json
    return json.dumps(value)


__all__ = ["Dispatcher", "recover_interrupted", "JobStatus"]

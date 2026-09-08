"""FastAPI application.

    uvicorn app.main:app --app-dir backend

Startup: apply database migrations, load the calibration bundle, fail jobs interrupted by
a previous shutdown, start the dispatcher.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.api import jobs, races, traces
from app.api.deps import AppState, session, state
from app.config import Settings, get_settings
from app.database.migrate import migrate
from app.database.models import Job, JobStatus
from app.database.seed import load_bundle, seed
from app.database.session import Database
from app.engine import engine
from app.workers.dispatcher import Dispatcher, recover_interrupted

log = logging.getLogger("f1sim")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        log.info(migrate(db.engine))
        bundle = load_bundle(settings.bundle_path)
        with db.session() as s:
            added = seed(s, bundle)
        recovered = recover_interrupted(db)
        log.info("data %s (%d new calibrations), %d interrupted jobs failed",
                 bundle["data_version"], added, recovered)
        dispatcher = Dispatcher(db, settings) if settings.dispatcher_enabled else None
        app.state.f1sim = AppState(settings=settings, db=db, dispatcher=dispatcher,
                                   engine_version=engine().__version__,
                                   data_version=bundle["data_version"],
                                   trace_slots=threading.BoundedSemaphore(
                                       settings.max_concurrent_traces))
        if dispatcher:
            dispatcher.start()
        try:
            yield
        finally:
            if dispatcher:
                dispatcher.stop()
            db.dispose()

    app = FastAPI(title="F1 Strategy Simulator API", version=engine().__version__,
                  lifespan=lifespan)
    app.include_router(races.router)
    app.include_router(jobs.router)
    app.include_router(traces.router)

    @app.get("/", include_in_schema=False)
    def root():
        # The API has no home page of its own; send people to its docs.
        return RedirectResponse("/docs")

    @app.get("/health", tags=["health"])
    def health(s=Depends(session), st: AppState = Depends(state)):
        pending = s.scalar(select(func.count()).select_from(Job)
                           .where(Job.status == JobStatus.pending))
        return {"status": "ok", "engine_version": st.engine_version,
                "data_version": st.data_version, "workers": st.settings.max_workers,
                "running": st.dispatcher.running if st.dispatcher else 0,
                "pending": pending, "max_pending": st.settings.max_pending_jobs}

    return app


app = create_app()

"""Lap-by-lap replay of one simulated race.

Unlike simulations and searches, a trace is served inline: it replays one
race and samples 200 more (single-threaded) to find a typical one, which
takes tens of milliseconds. It still goes through the same validation as a
job, and a semaphore bounds how many run at once (HTTP 429 beyond that), so
traces cannot starve the API or the worker pool.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import AppState, session, state
from app.engine import engine
from app.schemas.common import MODE_LABELS
from app.schemas.jobs import TraceOut, TraceRequest
from app.services import admission
from app.services.ratelimit import limit

router = APIRouter(tags=["traces"])

TYPICAL_SAMPLE = 200


@router.post("/traces", response_model=TraceOut,
             responses={429: {"description": "Too many traces running, or rate limited"}},
             dependencies=[Depends(limit("traces"))])
def race_trace(req: TraceRequest, s: Session = Depends(session), st: AppState = Depends(state)):
    race, cal = admission._common(s, req, st.data_version)
    admission._check_strategy(cal, req.strategy, "strategy")
    admission.check_safety_car_laps(race, req.safety_car_laps)
    if not st.trace_slots.acquire(blocking=False):
        return JSONResponse(status_code=429, headers={"Retry-After": "1"},
                            content={"detail": "too many race traces running; try again shortly"})
    try:
        out = json.loads(engine().trace(
            json.dumps(cal.config), req.driver, req.strategy, seed=req.seed,
            scenario=-1 if req.scenario is None else req.scenario, sample=TYPICAL_SAMPLE,
            sc_laps=req.safety_car_laps))
    finally:
        st.trace_slots.release()
    return TraceOut(**out, race_id=race.id, calibration_mode=req.mode,
                    mode_label=MODE_LABELS[req.mode], engine_version=st.engine_version,
                    data_version=st.data_version)

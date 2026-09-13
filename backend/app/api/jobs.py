from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AppState, session, state
from app.database.models import Job, JobStatus
from app.schemas.common import MODE_LABELS
from app.schemas.jobs import (JobAccepted, JobError, JobLinks, JobOut, OptimizationRequest,
                              OptimizationResult, ResultMetadata, SimulationRequest,
                              SimulationResult)
from app.services import admission
from app.services.ratelimit import limit

router = APIRouter(tags=["jobs"])


def links(job_id: uuid.UUID) -> JobLinks:
    return JobLinks(self=f"/jobs/{job_id}", result=f"/jobs/{job_id}/result")


def _test_action(st: AppState, value: str | None) -> str | None:
    return value if (value and st.settings.test_hooks) else None


def _submit(prepared: admission.Prepared, s: Session, st: AppState, response: Response):
    try:
        job = admission.admit(s, prepared, st.settings, st.engine_version, st.data_version)
    except admission.QueueFull:
        s.rollback()
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": "10"},
            content={"detail": f"the job queue is full ({st.settings.max_pending_jobs} pending); "
                               "try again shortly"})
    s.commit()
    if st.dispatcher:
        st.dispatcher.notify()
    response.headers["Location"] = f"/jobs/{job.id}"
    return JobAccepted(job_id=job.id, kind=job.kind.value, status=job.status.value,
                       links=links(job.id))


@router.post("/simulations", status_code=202, response_model=JobAccepted,
             responses={429: {"description": "Queue full or rate limited"}},
             dependencies=[Depends(limit("jobs"))])
def submit_simulation(req: SimulationRequest, response: Response, s: Session = Depends(session),
                      st: AppState = Depends(state),
                      x_test_action: str | None = Header(None, include_in_schema=False)):
    prepared = admission.prepare_simulation(s, req, st.settings, st.data_version,
                                            _test_action(st, x_test_action))
    return _submit(prepared, s, st, response)


@router.post("/optimizations", status_code=202, response_model=JobAccepted,
             responses={429: {"description": "Queue full or rate limited"}},
             dependencies=[Depends(limit("jobs"))])
def submit_optimization(req: OptimizationRequest, response: Response,
                        s: Session = Depends(session), st: AppState = Depends(state),
                        x_test_action: str | None = Header(None, include_in_schema=False)):
    prepared = admission.prepare_optimization(s, req, st.settings, st.data_version,
                                              _test_action(st, x_test_action))
    return _submit(prepared, s, st, response)


def _job_out(job: Job) -> JobOut:
    params = {k: v for k, v in job.params.items() if k != "test_action"}
    error = JobError(kind=job.error_kind, message=job.error_message) if job.error_kind else None
    return JobOut(id=job.id, kind=job.kind.value, status=job.status.value,
                  created_at=job.created_at, started_at=job.started_at,
                  finished_at=job.finished_at, race_id=job.race_id, driver=job.driver_code,
                  calibration_mode=job.calibration_mode.value,
                  mode_label=MODE_LABELS[job.calibration_mode.value], params=params,
                  engine_version=job.engine_version, data_version=job.data_version, error=error,
                  links=links(job.id))


def _get_job(s: Session, job_id: uuid.UUID) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail="unknown job")
    return job


@router.get("/jobs", response_model=list[JobOut], responses={404: {"description": "Disabled"}})
def list_jobs(status_: JobStatus | None = Query(None, alias="status"),
              limit: int = Query(20, ge=1, le=100), s: Session = Depends(session),
              st: AppState = Depends(state)):
    """Recent jobs from everyone. Off in production (F1SIM_EXPOSE_JOB_LIST):
    there a job is reachable only through its random id, which its submitter
    received."""
    if not st.settings.expose_job_list:
        raise HTTPException(404, detail="job listing is disabled on this server")
    q = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status_:
        q = q.where(Job.status == status_)
    return [_job_out(j) for j in s.scalars(q)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, s: Session = Depends(session)):
    return _job_out(_get_job(s, job_id))


@router.get("/jobs/{job_id}/result", response_model=SimulationResult | OptimizationResult,
            responses={409: {"description": "Job not completed"}})
def get_result(job_id: uuid.UUID, s: Session = Depends(session)):
    job = _get_job(s, job_id)
    if job.status != JobStatus.completed:
        detail = {"status": job.status.value}
        if job.error_kind:
            detail["error"] = {"kind": job.error_kind, "message": job.error_message}
        raise HTTPException(409, detail=detail)
    cal = job.calibration
    meta = ResultMetadata(
        calibration_mode=job.calibration_mode.value,
        mode_label=MODE_LABELS[job.calibration_mode.value],
        engine_version=job.engine_version, data_version=job.data_version,
        seed=job.params["seed"], scenarios=job.params["scenarios"], calibration_id=cal.id,
        tires=[{"designation": t.designation, "absolute_compound": t.absolute_compound,
                "confidence": t.confidence, "clean_laps": t.clean_laps,
                "max_stint_laps": t.max_stint_laps} for t in cal.tires],
        provenance=cal.provenance)
    body = {**job.result, "job_id": job.id, "metadata": meta}
    if job.kind.value == "simulation":
        return SimulationResult.model_validate(body)
    return OptimizationResult.model_validate(body)

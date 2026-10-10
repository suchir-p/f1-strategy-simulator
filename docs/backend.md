# Backend design

FastAPI + PostgreSQL service that runs the C++ engine as background jobs. It
keeps the engine's guarantees (reproducibility, matched scenarios) and its
caveats (tire confidence, calibration mode) intact all the way to the API.

```
client ──POST──▶ FastAPI ──validate──▶ engine checks (strategy valid? search size?)
                    │
                    └─admit (advisory lock, pending limit) ──▶ jobs table: pending
                                                                     │
dispatcher thread ──claim (FOR UPDATE SKIP LOCKED)──▶ running ──▶ spawn process pool
                                                                     │  pybind11 → C++ / OpenMP
                    jobs table: completed / failed  ◀── result or sanitized error
```

## Calibration modes

| Mode | Uses | Label |
| --- | --- | --- |
| `pre_race` (default) | Races strictly before this one, plus this weekend's grid, qualifying, tire nomination and distance. Rivals run a reference strategy computed from the same pre-race config. | Pre-race recommendation |
| `race_fitted` | Parameters fitted on the race itself; rivals on their actual strategies. | Retrospective analysis |

`pre_race` exists for 15 of 20 races; the first five have too little history
(the minimum is five earlier races). Pit loss and safety-car lap time are
measured at the event, standing in for prior-year values; the provenance
record lists them under `event_measured_track_constants`. Every result carries
its mode, label, provenance and the confidence level of each tire compound, so
a retrospective result can't be shown as a pre-race one by accident.

`data/tests/test_prerace_integrity.py` scrambles a race's outcome (lap times,
fitted pace and tires, actual strategies, result, safety cars) and rebuilds
its pre-race config: only the declared track constants may change. It also
scrambles every later race and requires no change at all. The test fails on
the earlier prediction pipeline, which gave rivals their actual strategies.

## Reproducibility

Each job stores its complete effective configuration in `jobs.params`,
including the seed (generated and stored if the client omits one), plus the
engine version, the data version (a hash of the calibration bundle) and the
exact calibration row it ran against. Re-submitting the same parameters and
seed returns identical results, and old calibrations are kept when a new
bundle is loaded, so past jobs stay traceable.

## Execution and limits

* **Workers:** `F1SIM_MAX_WORKERS` processes (default 1), each with
  `F1SIM_OMP_THREADS_PER_WORKER` OpenMP threads (default 2). Keep
  workers × threads ≤ CPU cores. Processes use the `spawn` start method:
  forking after the OpenMP runtime has started can deadlock.
* **Queue:** PostgreSQL. Admission takes a transaction-scoped advisory lock,
  counts pending jobs and returns `429` with `Retry-After` beyond
  `F1SIM_MAX_PENDING_JOBS` (default 20). The integration test sends 12
  concurrent requests against a limit of 3; with the lock removed, 4–9 get in.
* **Size:** jobs larger than `F1SIM_MAX_RACES_PER_JOB` (candidates ×
  scenarios, default 1,000,000) are refused with `422` before they run. A
  running engine call can't be cancelled cleanly, so it's better to refuse
  work than to start it.

## Race traces

`POST /traces` replays one simulated race lap by lap. It is the one engine
call served inline rather than queued: it runs one race, plus 200 more
(single-threaded, tens of milliseconds) when it has to find a typical one. It
goes through the same validation as a job, and a semaphore
(`F1SIM_MAX_CONCURRENT_TRACES`, default 2) bounds how many run at once; beyond
that it returns `429`. Because randomness is keyed by scenario id, a trace of
scenario *k* with seed *s* is exactly race *k* of a simulation with seed *s*.

## Job states and failures

`pending → running → completed`, or `failed` from either. Error kinds:

| Kind | Cause | Message |
| --- | --- | --- |
| `invalid_input` | engine rejected the input (`ValueError`) | the engine's own message |
| `engine_error` | any other exception, including C++ errors | generic, with the job id; details in the server log |
| `worker_crashed` | worker process died | generic; a new pool is started |
| `interrupted` | server stopped while the job ran | marked at the next startup |

Pending jobs survive a restart and run afterwards. There is no automatic
retry and no exactly-once guarantee. If a worker dies, Python's process pool
fails every job running in that pool at the time. The design assumes a single
API instance.

## Schema

`races`, `drivers`, `race_drivers`, `calibrations` (config and provenance as
JSONB, unique per race, mode and data version), `tire_calibrations`
(per-compound parameters and confidence), `jobs` (status, params, versions,
result JSONB, error). Only aggregated results are stored, never per-lap or
per-scenario rows. Tables are created at startup; migrations (Alembic) are a
later addition.

## Running the API locally

```bash
docker compose up -d db                                   # just PostgreSQL 16
pip install -r backend/requirements.txt
cmake -S engine -B engine/build -DF1SIM_BUILD_PYTHON=ON \
      -Dpybind11_DIR=$(python3 -c "import pybind11; print(pybind11.get_cmake_dir())")
cmake --build engine/build -j
cd backend && python -m uvicorn app.main:app --reload     # http://localhost:8000/docs
```

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /races` | Races, and which calibration modes each one supports |
| `GET /races/{id}/drivers` | Drivers and starting grid |
| `GET /races/{id}/tires?mode=` | Tire parameters, confidence levels and provenance |
| `POST /simulations` | Simulate one strategy for one driver (returns a job) |
| `POST /optimizations` | Grid-search strategies for one driver (returns a job) |
| `POST /traces` | Replay one simulated race lap by lap (inline, bounded) |
| `GET /jobs/{id}` | Status, full effective parameters including the seed, errors |
| `GET /jobs` | Recent jobs (development only; hidden in production) |
| `GET /jobs/{id}/result` | Outcome, rankings, warnings and metadata |
| `GET /health` | Versions, worker and queue status |

## Guarantees

* **Two labeled modes.** `pre_race` (default) uses only races before the one
  requested plus that weekend's grid and qualifying; rivals run a reference
  strategy, not their real one. `race_fitted` is retrospective. A test
  scrambles a race's outcome, and every later race, and checks the pre-race
  config doesn't change.
* **Reproducible.** Every job stores its full configuration, seed, engine
  version and data version; the same inputs and seed give identical results.
* **Bounded.** Fixed worker pool (spawned processes, capped OpenMP threads),
  a pending-job limit enforced under concurrency (`429`), and oversized jobs
  refused up front (`422`).
* **Honest failures.** Engine errors, worker crashes and restarts end in
  explicit `failed` states with sanitized messages; pending jobs survive a
  restart.
* **Caveats preserved.** Results carry tire confidence levels, strategy
  warnings, the outcome split into finish probability and position-if-finished,
  and provenance.
* **Migrations.** The schema is versioned with Alembic and upgraded at
  startup under an advisory lock; databases created before migrations existed
  are adopted in place.
* **Production mode.** `F1SIM_ENV=production` refuses default database
  credentials, hides the job list and rate-limits submissions per client
  ([deployment.md](deployment.md)).

## Request options

Simulations and searches also take `safety_car_laps` (a what-if: those laps
run under the safety car in every simulated race), and a simulation can take
`compare_with`, a second strategy run on the same races for a paired
comparison. Search results include every candidate's score (`landscape`) for
charting the pit window.

## Tests

Tests (31 integration tests against real PostgreSQL, worker processes and the
engine, plus 8 data-pipeline tests including pre-race integrity):

```bash
python -m pytest data/tests
cd backend && python -m pytest        # needs the docker-compose database
```

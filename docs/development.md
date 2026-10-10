# Development

## Repository layout

```
engine/            C++ engine (library, CLI, benchmark, tests, pybind11 module)
backend/           FastAPI + PostgreSQL job service (app/, migrations/, tests/)
frontend/          Next.js dashboard (components/, lib/, tests/, e2e/)
data/
  calibration/     FastF1 -> race config calibration, plus raw-lap snapshots
  bundle/          calibration bundle served by the backend, and its builder
  validation/      in-sample reproduction checks
  prediction/      out-of-sample prediction study
  robustness/      strategy robustness study
  tests/           pre-race integrity and data-pipeline tests
scripts/           bench.sh, release.sh
docs/              model, backend design, performance, deployment
results/           benchmark results by machine
```

## Build from source

Requires CMake ≥ 3.20, a C++20 compiler with OpenMP (GCC 11+, or Clang with
libomp), and internet access on first configure (GoogleTest and nlohmann/json
are fetched automatically).

```bash
cmake -S engine -B engine/build
cmake --build engine/build -j
ctest --test-dir engine/build --output-on-failure
```

macOS with Apple Clang: `brew install libomp`, then add
`-DOpenMP_ROOT=$(brew --prefix libomp)` to the first command.

Sanitizer build: add `-DF1SIM_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug`.

Python module:

```bash
pip install pybind11
cmake -S engine -B engine/build -DF1SIM_BUILD_PYTHON=ON \
      -Dpybind11_DIR=$(python3 -c "import pybind11; print(pybind11.get_cmake_dir())")
cmake --build engine/build -j
PYTHONPATH=engine/build python3 -c "import f1sim; print(f1sim.__doc__)"
```

## Command-line interface

```bash
CLI=engine/build/f1sim_cli
RACE=data/examples/synthetic_race.json

$CLI trace    --config $RACE                                # one deterministic race, lap by lap
$CLI simulate --config $RACE --driver LEC --strategy M-24-H --scenarios 5000
$CLI field    --config $RACE --scenarios 4000               # whole-field outcome distribution
$CLI optimize --config $RACE --driver LEC --scenarios 1000 --no-two-stop
$CLI optimize --config $RACE --driver LEC --scenarios 500 --lap-step 3 --window 10:40 --max-candidates 2000
```

Strategies are written as `M-24-H` (start on mediums, pit at the end of lap
24 for hards) or `S-12-M-35-H`.

## Running the services without Docker

* API: see [backend.md](backend.md#running-the-api-locally)
* Dashboard: see [dashboard.md](dashboard.md)

## Tests

```bash
ctest --test-dir engine/build --output-on-failure   # engine (GoogleTest)
python -m pytest data/tests                         # data pipeline, pre-race integrity
cd backend && python -m pytest                      # API integration tests; needs the docker-compose database
cd frontend && npm test                             # strategy and chart logic (Vitest)
cd frontend && npm run e2e                          # end to end (Playwright), needs the API running
```

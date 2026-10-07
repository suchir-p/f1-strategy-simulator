# API image: FastAPI app + the compiled C++ engine + the calibration bundle.
# Build from the repository root:  docker build -f backend/Dockerfile .

# ---- stage 1: build the engine's Python module ---------------------------------
FROM python:3.12-slim AS engine
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ make cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "pybind11==2.13.6"
WORKDIR /src
COPY VERSION ./VERSION
COPY engine ./engine
RUN cmake -S engine -B engine/build -DCMAKE_BUILD_TYPE=Release \
        -DF1SIM_BUILD_TESTS=OFF -DF1SIM_BUILD_PYTHON=ON \
        -Dpybind11_DIR="$(python -c 'import pybind11; print(pybind11.get_cmake_dir())')" \
 && cmake --build engine/build -j --target f1sim_py

# ---- stage 2: runtime --------------------------------------------------------------
FROM python:3.12-slim
# libgomp: the OpenMP runtime the engine links against.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 f1sim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/migrations backend/migrations
COPY backend/alembic.ini backend/alembic.ini
COPY data/bundle/2024_bundle.json data/bundle/2024_bundle.json
COPY --from=engine /src/engine/build/f1sim*.so engine/build/
USER f1sim
WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

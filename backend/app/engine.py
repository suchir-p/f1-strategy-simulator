"""Loads the compiled C++ engine (pybind11 module `f1sim`).

The API process imports it only for cheap checks (strategy validation and
candidate counting). Simulations run in separate worker processes.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType


def load_engine(engine_path: Path) -> ModuleType:
    path = str(engine_path)
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import f1sim
    except ImportError as exc:  # pragma: no cover - configuration error
        raise RuntimeError(
            f"cannot import the f1sim engine module from {engine_path}; build it with "
            "-DF1SIM_BUILD_PYTHON=ON (see README)") from exc
    return f1sim


@lru_cache
def engine() -> ModuleType:
    from app.config import get_settings
    return load_engine(get_settings().engine_path)

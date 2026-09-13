"""Code that runs inside worker processes.

Workers are started with the `spawn` method (never `fork`): forking a process
whose OpenMP runtime is already initialized can deadlock. Each worker imports
the engine once in `init_worker` and caps its OpenMP thread count.

Everything here must be importable at module level so spawned processes can
find it, and must not touch the database: the dispatcher passes in everything
a job needs and stores what comes back.
"""

from __future__ import annotations

import json
import os
import time
from types import ModuleType

_engine: ModuleType | None = None


def init_worker(engine_path: str, omp_threads: int) -> None:
    os.environ["OMP_NUM_THREADS"] = str(omp_threads)
    global _engine
    from app.engine import load_engine
    from pathlib import Path
    _engine = load_engine(Path(engine_path))


def outcome(stats: dict) -> dict:
    hist = stats["histogram"]
    finish = hist[:-1]
    total = sum(finish)
    dist = [x / total for x in finish] if total > 0 else [0.0] * len(finish)
    return {
        "finish_probability": stats["finish_probability"],
        "mean_position_if_finished": stats["mean_position_if_finished"],
        "p05_if_finished": stats["p05_if_finished"],
        "p50_if_finished": stats["p50_if_finished"],
        "p95_if_finished": stats["p95_if_finished"],
        "position_distribution_if_finished": dist,
        "p_win": stats["p_win"],
        "p_podium": stats["p_podium"],
        "p_points": stats["p_points"],
        "mean_scored_position": stats["mean_position"],
    }


def candidate(c: dict) -> dict:
    return {
        "rank": c["rank"],
        "strategy": c["strategy"]["label"],
        "outcome": outcome(c["stats"]),
        "comparison_vs_best": {"diff_vs_best": c["diff_vs_best"], "ci95": c["diff_ci_95"],
                               "inconclusive": c["inconclusive_vs_best"]},
        "notes": c["notes"],
    }


def _test_action(action: str | None) -> None:
    """Failure injection for integration tests (enabled by F1SIM_TEST_HOOKS)."""
    if not action:
        return
    if action.startswith("sleep:"):
        time.sleep(float(action.split(":", 1)[1]))
    elif action == "crash":
        os._exit(13)  # Simulates a native crash: the process dies without cleanup.
    elif action == "raise":
        raise RuntimeError("injected failure with internal details /secret/path")
    elif action == "native_error":
        _engine.validate("{not valid json")  # C++ parse error crossing the boundary.


def run_job(payload: dict) -> dict:
    assert _engine is not None, "worker not initialized"
    _test_action(payload.get("test_action"))
    race_json = json.dumps(payload["race_config"])
    p = payload["params"]
    if payload["kind"] == "simulation":
        out = json.loads(_engine.simulate(race_json, p["driver"], p["strategy"],
                                          scenarios=p["scenarios"], seed=p["seed"],
                                          threads=payload["threads"],
                                          sc_laps=p.get("safety_car_laps", []),
                                          compare_with=p.get("compare_with") or ""))
        result = {"kind": "simulation", "driver": out["driver"],
                  "strategy": out["strategy"]["label"], "outcome": outcome(out["stats"]),
                  "notes": out["notes"], "safety_car_laps": p.get("safety_car_laps", [])}
        if "comparison" in out:
            c = out["comparison"]
            result["comparison"] = {"strategy": c["strategy"]["label"],
                                    "outcome": outcome(c["stats"]), "notes": c["notes"],
                                    "diff": c["diff"], "ci95": c["diff_ci_95"]}
        return result

    s = p["search"]
    out = json.loads(_engine.optimize(
        race_json, p["driver"], scenarios=p["scenarios"], seed=p["seed"],
        threads=payload["threads"], min_stint_laps=s["min_stint_laps"], lap_step=s["lap_step"],
        window_start=s["window_start"], window_end=s["window_end"], one_stop=s["one_stop"],
        two_stop=s["two_stop"], max_candidates=s["max_candidates"], top_k=p["top_k"],
        extra_strategies=p["compare_strategies"], sc_laps=p.get("safety_car_laps", [])))
    return {"kind": "optimization", "driver": out["driver"],
            "candidates_evaluated": out["candidates_evaluated"],
            "ranked": [candidate(c) for c in out["ranked"]],
            "compared_strategies": [candidate(c) for c in out["extra_strategies"]],
            "landscape": out["landscape"], "safety_car_laps": p.get("safety_car_laps", [])}

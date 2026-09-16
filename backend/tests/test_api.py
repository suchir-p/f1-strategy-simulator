"""API behavior: races, results, metadata preservation, reproducibility, validation."""

from __future__ import annotations

import json

from conftest import SIM

from app.config import get_settings

BUNDLE = json.loads(get_settings().bundle_path.read_text())


def bundle_race(race_id: str) -> dict:
    return next(r for r in BUNDLE["races"] if r["id"] == race_id)


def submit(srv, path: str, body: dict, **headers) -> str:
    r = srv.client.post(path, json=body, headers=headers)
    assert r.status_code == 202, r.text
    assert r.headers["location"] == f"/jobs/{r.json()['job_id']}"
    return r.json()["job_id"]


def run(srv, path: str, body: dict) -> dict:
    job_id = submit(srv, path, body)
    job = srv.wait(job_id)
    assert job["status"] == "completed", job
    return srv.client.get(f"/jobs/{job_id}/result").json()


# ------------------------------------------------------------------ races ----

def test_races_and_mode_availability(server):
    races = server.client.get("/races").json()
    assert len(races) == 20
    by_id = {r["id"]: r for r in races}
    assert by_id["2024-bahrain"]["modes"]["pre_race"]["available"] is False
    assert "earlier races" in by_id["2024-bahrain"]["modes"]["pre_race"]["reason"]
    assert by_id["2024-italy"]["modes"]["pre_race"]["available"] is True
    assert sum(r["modes"]["pre_race"]["available"] for r in races) == 15
    assert all(r["modes"]["race_fitted"]["available"] for r in races)
    drivers = server.client.get("/races/2024-italy/drivers").json()
    assert len(drivers) == 20 and drivers[0]["grid_position"] == 1


def test_drivers_hide_race_outcomes_before_the_race(server):
    pre = server.client.get("/races/2024-italy/drivers?mode=pre_race").json()
    reference = bundle_race("2024-italy")["calibrations"]["pre_race"]["provenance"]["rival_strategy"]
    assert {d["default_strategy"] for d in pre} == {reference["label"]}
    assert all(d["actual_strategy"] is None and d["actual_position"] is None for d in pre)
    retro = {d["code"]: d for d in server.client.get("/races/2024-italy/drivers?mode=race_fitted").json()}
    assert retro["LEC"]["actual_position"] == 1 and retro["LEC"]["actual_strategy"] == "M-15-H"
    assert retro["LEC"]["default_strategy"] == "M-15-H"


def test_tire_metadata_matches_bundle(server):
    for mode in ("pre_race", "race_fitted"):
        out = server.client.get(f"/races/2024-italy/tires?mode={mode}").json()
        expected = bundle_race("2024-italy")["calibrations"][mode]["tires"]
        got = [{k: c[k] for k in expected[0]} for c in out["compounds"]]
        assert got == expected
        assert out["data_version"] == BUNDLE["data_version"]
    assert server.client.get("/races/2024-bahrain/tires?mode=pre_race").status_code == 404


# ---------------------------------------------------------------- results ----

def test_simulation_result_keeps_metadata(server):
    res = run(server, "/simulations", SIM)
    out = res["outcome"]
    assert 0 < out["finish_probability"] <= 1
    assert abs(sum(out["position_distribution_if_finished"]) - 1) < 1e-9
    assert out["p05_if_finished"] <= out["p50_if_finished"] <= out["p95_if_finished"] <= 20
    meta = res["metadata"]
    assert meta["calibration_mode"] == "pre_race"
    assert meta["mode_label"] == "Pre-race recommendation"
    assert meta["data_version"] == BUNDLE["data_version"]
    assert meta["engine_version"] == server.state.engine_version
    # Tire confidence and warnings survive C++ -> Python -> PostgreSQL -> API.
    expected_tires = bundle_race("2024-italy")["calibrations"]["pre_race"]["tires"]
    assert [t["confidence"] for t in meta["tires"]] == [t["confidence"] for t in expected_tires]
    assert any("estimated from other races" in n for n in res["notes"])
    assert meta["provenance"]["rival_strategy"]["label"]


def test_retrospective_mode_is_labelled(server):
    res = run(server, "/simulations", {**SIM, "mode": "race_fitted"})
    assert res["metadata"]["calibration_mode"] == "race_fitted"
    assert res["metadata"]["mode_label"] == "Retrospective analysis"


def test_same_seed_same_result(server):
    a = run(server, "/simulations", {**SIM, "seed": 1234})
    b = run(server, "/simulations", {**SIM, "seed": 1234})
    for r in (a, b):
        r.pop("job_id")
    assert a == b
    c = run(server, "/simulations", {**SIM, "seed": 1235})
    assert c["outcome"] != a["outcome"]


def test_generated_seed_is_stored_and_replayable(server):
    job_id = submit(server, "/simulations", SIM)
    job = server.wait(job_id)
    assert job["params"]["seed_generated"] is True
    seed = job["params"]["seed"]
    first = server.client.get(f"/jobs/{job_id}/result").json()
    again = run(server, "/simulations", {**SIM, "seed": seed})
    assert first["outcome"] == again["outcome"]
    assert first["metadata"]["seed"] == seed


def test_optimization_ranks_and_compares(server):
    body = {"race_id": "2024-italy", "driver": "LEC", "scenarios": 100, "seed": 5,
            "search": {"lap_step": 6, "two_stop": False}, "top_k": 3,
            "compare_strategies": ["M-15-H"]}
    job_id = submit(server, "/optimizations", body)
    job = server.wait(job_id)
    res = server.client.get(f"/jobs/{job_id}/result").json()
    assert res["candidates_evaluated"] == job["params"]["candidates"]
    assert [c["rank"] for c in res["ranked"]] == [1, 2, 3]
    assert res["ranked"][0]["comparison_vs_best"]["diff_vs_best"] == 0
    compared = res["compared_strategies"]
    assert [c["strategy"] for c in compared] == ["M-15-H"]
    lo, hi = compared[0]["comparison_vs_best"]["ci95"]
    assert lo <= compared[0]["comparison_vs_best"]["diff_vs_best"] <= hi
    assert all(c["notes"] for c in res["ranked"])  # Pre-race Monza tires are all pooled/fallback.


# ------------------------------------------------------------- validation ----

def test_validation_errors(server):
    post = server.client.post
    assert post("/simulations", json={**SIM, "race_id": "2024-monaco"}).status_code == 404
    r = post("/simulations", json={**SIM, "driver": "XYZ"})
    assert r.status_code == 422 and "did not take part" in r.json()["detail"]
    r = post("/simulations", json={**SIM, "strategy": "M-20-M"})
    assert r.status_code == 422 and "different dry compounds" in r.json()["detail"]
    r = post("/simulations", json={**SIM, "strategy": "M-60-H"})
    assert r.status_code == 422
    assert post("/simulations", json={**SIM, "strategy": "banana"}).status_code == 422
    assert post("/simulations", json={**SIM, "scenarios": 10**7}).status_code == 422
    r = post("/simulations", json={**SIM, "race_id": "2024-bahrain", "driver": "VER"})
    assert r.status_code == 422 and "pre-race" in r.json()["detail"]
    r = post("/optimizations", json={"race_id": "2024-italy", "driver": "LEC",
                                     "search": {"lap_step": 1, "max_candidates": 50}})
    assert r.status_code == 422 and "more than 50 candidates" in r.json()["detail"]
    r = post("/optimizations", json={"race_id": "2024-italy", "driver": "LEC", "scenarios": 5000,
                                     "search": {"lap_step": 2, "max_candidates": 3000}})
    assert r.status_code == 422 and "job too large" in r.json()["detail"]
    r = post("/optimizations", json={"race_id": "2024-italy", "driver": "LEC",
                                     "search": {"one_stop": False, "two_stop": False}})
    assert r.status_code == 422


def test_root_redirects_to_docs(server):
    r = server.client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_unknown_job_and_unfinished_result(server):
    assert server.client.get("/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
    server.state.dispatcher.pause()
    try:
        job_id = submit(server, "/simulations", SIM)
        r = server.client.get(f"/jobs/{job_id}/result")
        assert r.status_code == 409 and r.json()["detail"]["status"] == "pending"
    finally:
        server.state.dispatcher.resume()
    assert server.wait(job_id)["status"] == "completed"


def test_test_hooks_header_ignored_when_disabled(fresh_server):
    with fresh_server(test_hooks=False) as srv:
        job_id = submit(srv, "/simulations", SIM, **{"X-Test-Action": "crash"})
        assert srv.wait(job_id)["status"] == "completed"
        assert "test_action" not in srv.client.get(f"/jobs/{job_id}").json()["params"]


# ------------------------------------------------- comparisons and what-ifs ----

def test_paired_comparison_matches_separate_runs(server):
    """Comparing A with B runs both on the same races: each side equals a plain
    simulation with the same seed, and the interval brackets the difference."""
    body = {**SIM, "strategy": "M-20-H", "scenarios": 300, "seed": 11}
    res = run(server, "/simulations", {**body, "compare_with": "H-30-M"})
    alone_a = run(server, "/simulations", body)
    alone_b = run(server, "/simulations", {**body, "strategy": "H-30-M"})
    c = res["comparison"]
    assert c["strategy"] == "H-30-M"
    assert res["outcome"] == alone_a["outcome"] and c["outcome"] == alone_b["outcome"]
    expected = (res["outcome"]["mean_scored_position"] - c["outcome"]["mean_scored_position"])
    assert abs(c["diff"] - expected) < 1e-9
    assert c["ci95"][0] <= c["diff"] <= c["ci95"][1]
    assert alone_a.get("comparison") is None
    r = server.client.post("/simulations", json={**body, "compare_with": "M-20-H"})
    assert r.status_code == 422


def test_forced_safety_car_changes_the_result_and_is_recorded(server):
    body = {**SIM, "strategy": "M-20-H", "scenarios": 200, "seed": 3}
    base = run(server, "/simulations", body)
    what_if = run(server, "/simulations", {**body, "safety_car_laps": [32, 30, 31, 31]})
    assert what_if["safety_car_laps"] == [30, 31, 32]
    assert base["safety_car_laps"] == []
    assert what_if["outcome"] != base["outcome"]
    r = server.client.post("/simulations", json={**body, "safety_car_laps": [80]})
    assert r.status_code == 422 and "53 laps" in r.json()["detail"]


def test_optimization_returns_the_whole_landscape(server):
    body = {"race_id": "2024-italy", "driver": "LEC", "scenarios": 100, "seed": 5,
            "search": {"lap_step": 6, "two_stop": False}, "top_k": 3,
            "safety_car_laps": [20, 21]}
    res = run(server, "/optimizations", body)
    land = res["landscape"]
    assert len(land) == res["candidates_evaluated"]
    assert land[0][0] == res["ranked"][0]["strategy"]
    assert [x[1] for x in land] == sorted(x[1] for x in land)
    assert res["safety_car_laps"] == [20, 21]


# ------------------------------------------------------------------ traces ----

TRACE = {"race_id": "2024-italy", "driver": "LEC", "strategy": "M-24-H", "seed": 9}


def test_typical_trace_finishes_at_the_median(server):
    r = server.client.post("/traces", json=TRACE)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["typical"]["sample_size"] == 200
    lec = next(d for d in t["drivers"] if d["code"] == "LEC")
    assert lec["strategy"] == "M-24-H" and lec["finished"]
    assert lec["position"] == t["typical"]["median_position_if_finished"]
    assert len(lec["positions"]) == t["total_laps"] + 1
    assert lec["positions"][0] == lec["grid"] and lec["positions"][-1] == lec["position"]
    assert [p["lap"] for p in lec["pits"]] == [24] and lec["pits"][0]["compound"] == "H"
    assert t["mode_label"] == "Pre-race recommendation"
    # Replaying the same scenario explicitly gives the same race.
    again = server.client.post("/traces", json={**TRACE, "scenario": t["scenario"]}).json()
    assert again["drivers"] == t["drivers"] and again["typical"] is None


def test_trace_what_if_safety_car_and_validation(server):
    t = server.client.post("/traces", json={**TRACE, "scenario": 4,
                                            "safety_car_laps": [12, 13, 14]}).json()
    assert {12, 13, 14} <= set(t["safety_car_laps"])
    assert t["forced_safety_car_laps"] == [12, 13, 14]
    post = server.client.post
    assert post("/traces", json={**TRACE, "strategy": "M-20-M"}).status_code == 422
    assert post("/traces", json={**TRACE, "safety_car_laps": [70]}).status_code == 422
    assert post("/traces", json={**TRACE, "race_id": "nope"}).status_code == 404


def test_traces_are_bounded(server):
    """Traces run inline, so a semaphore caps them; when it is exhausted the
    API answers 429 instead of queueing work in the request threads."""
    slots = server.state.trace_slots
    held = 0
    while slots.acquire(blocking=False):
        held += 1
    try:
        r = server.client.post("/traces", json=TRACE)
        assert r.status_code == 429 and r.headers["retry-after"] == "1"
    finally:
        for _ in range(held):
            slots.release()
    assert server.client.post("/traces", json=TRACE).status_code == 200


def test_one_version_everywhere(server):
    """VERSION is the single source: the engine (built from it), the API and
    the dashboard's package all report the same version."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    version = (root / "VERSION").read_text().strip()
    web = json.loads((root / "frontend" / "package.json").read_text())["version"]
    health = server.client.get("/health").json()
    assert health["engine_version"] == version
    assert server.client.get("/openapi.json").json()["info"]["version"] == version
    assert web == version

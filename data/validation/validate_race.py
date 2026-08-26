"""Compare simulated outcomes with what actually happened in a race.

Usage:
    python data/validation/validate_race.py 2024_bahrain [2024_spain ...]
        [--scenarios 3000] [--cli engine/build/f1sim_cli]

For each race it runs the full field on its historical strategies and reports:
  * Spearman rank correlation between mean simulated position and the real
    finishing order, against a naive "finish where you started" baseline
  * mean absolute position error, against the same baseline
  * coverage: share of drivers whose real position falls inside the simulated
    5th-95th percentile range (~90% means well-calibrated spread; much
    higher means the spread is too wide, much lower too narrow)

Drivers who really retired are excluded from the metrics, because the
simulator's mean position mixes finishes and DNFs.

The race's real safety-car laps are replayed (no random safety cars), since
the question is whether the model reproduces the race given what happened.

Caveat: driver base pace is fitted on the same race, so this is an IN-SAMPLE
check of the race model (interactions, pit stops, tire wear). It shows the
simulator can reproduce a race, not that it can predict one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data" / "calibration" / "output"
sys.path.insert(0, str(ROOT / "data" / "calibration"))
import calibrate_race as cr  # noqa: E402


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    r = [0.0] * len(values)
    i = 0
    while i < len(order):  # Average ranks for ties.
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    var = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return cov / var if var else 0.0


def _runs(laps: list[int]) -> list[list[int]]:
    """[3, 4, 5, 9] -> [[3, 3], [9, 1]] as [start lap, length]."""
    out: list[list[int]] = []
    for lap in sorted(laps):
        if out and lap == out[-1][0] + out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([lap, 1])
    return out


def neutralized_laps(raw, winner: str) -> dict[str, list[int]]:
    """Laps the winner ran under the safety car ("sc") and virtual safety car
    ("vsc"), from FastF1's track status codes (4 = SC, 6/7 = VSC). A VSC that
    becomes a safety car on the next lap counts as the safety car only."""
    rows = raw[raw["Driver"] == winner]
    status = {}
    for _, r in rows.iterrows():
        if r["LapNumber"] != r["LapNumber"]:  # NaN
            continue
        s = str(r["TrackStatus"]) if r["TrackStatus"] == r["TrackStatus"] else ""
        status[int(r["LapNumber"])] = set(s[:-2] if s.endswith(".0") else s)
    sc = [n for n, s in status.items() if "4" in s]
    vsc = [n for n, s in status.items() if ("6" in s or "7" in s) and "4" not in s]
    sc_starts = {start for start, _ in _runs(sc)}
    for start, length in _runs(vsc):
        if start + length in sc_starts:
            vsc = [n for n in vsc if not start <= n < start + length]
    return {"sc": sorted(sc), "vsc": sorted(vsc)}


def real_vsc_laps(stem: str) -> list[int]:
    """Laps the real race ran under the virtual safety car, from the lap snapshot."""
    raw, _, _ = cr.load_snapshot(stem)
    session = json.loads((OUTPUT / f"{stem}_session.json").read_text())
    winner = next(r["Abbreviation"] for r in session["results"] if r["Position"] == 1)
    return neutralized_laps(raw, winner)["vsc"]


def validate(stem: str, scenarios: int, cli: str) -> dict:
    config = OUTPUT / f"{stem}.json"
    result = json.loads((OUTPUT / f"{stem}_result.json").read_text())
    # Replay the race's real safety-car laps instead of random ones: this test
    # asks whether the model reproduces the race given what actually happened.
    cfg_replay = json.loads(config.read_text())
    cfg_replay["safety_car"]["start_probability_per_lap"] = 0.0
    cfg_replay.setdefault("virtual_safety_car", {})["start_probability_per_lap"] = 0.0
    sc_laps = ",".join(str(x) for x in result["safety_car_laps"])
    vsc_laps = ",".join(str(x) for x in real_vsc_laps(stem))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(cfg_replay, fh)
        replay_path = fh.name
    cmd = [cli, "field", "--config", replay_path, "--scenarios", str(scenarios)]
    if sc_laps:
        cmd += ["--sc-laps", sc_laps]
    if vsc_laps:
        cmd += ["--vsc-laps", vsc_laps]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    Path(replay_path).unlink()
    sim = {d["driver"]: d for d in json.loads(proc.stdout)["drivers"]}

    finished = [c for c in result["classification"]
                if c["position"] is not None and c["status"] in ("Finished", "Lapped")
                or str(c["status"]).startswith("+")]
    cfg = json.loads(config.read_text())
    source = {d["code"]: d.get("strategy_source", "observed") for d in cfg["drivers"]}
    codes = [c["driver"] for c in finished if c["driver"] in sim]
    observed = [c for c in codes if source.get(c) == "observed"]
    actual = {c["driver"]: c["position"] for c in finished}
    grid = {c["driver"]: c["grid"] or 20 for c in finished}

    pred = [sim[c]["mean_position"] for c in codes]
    act = [actual[c] for c in codes]
    base = [grid[c] for c in codes]
    covered = sum(sim[c]["p05"] <= actual[c] <= sim[c]["p95"] for c in codes)
    misses = sorted(codes, key=lambda c: -abs(sim[c]["mean_position"] - actual[c]))[:3]
    return {
        "race": result["race"],
        "drivers_compared": len(codes),
        "spearman_sim": spearman(pred, act),
        "spearman_grid": spearman(base, act),
        "mae_sim": sum(abs(p - a) for p, a in zip(pred, act)) / len(codes),
        "mae_grid": sum(abs(g - a) for g, a in zip(base, act)) / len(codes),
        "coverage_p05_p95": covered / len(codes),
        # Same metrics restricted to drivers whose strategy was fully observed.
        "drivers_observed": len(observed),
        "spearman_sim_observed": spearman([sim[c]["mean_position"] for c in observed],
                                          [actual[c] for c in observed]),
        "mae_sim_observed": sum(abs(sim[c]["mean_position"] - actual[c]) for c in observed) / len(observed),
        "biggest_misses": [
            {"driver": c, "simulated_mean": round(sim[c]["mean_position"], 2),
             "actual": actual[c], "grid": grid[c]} for c in misses],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stems", nargs="+", help="e.g. 2024_bahrain")
    ap.add_argument("--scenarios", type=int, default=3000)
    ap.add_argument("--cli", default=str(ROOT / "engine" / "build" / "f1sim_cli"))
    ap.add_argument("--json", help="also write the report to this file")
    args = ap.parse_args()

    reports = [validate(s, args.scenarios, args.cli) for s in args.stems]
    print(f"{'race':28s} {'n':>3s} {'rho sim':>8s} {'rho grid':>9s} {'MAE sim':>8s} {'MAE grid':>9s} {'cover':>6s}")
    for r in reports:
        print(f"{r['race']:28s} {r['drivers_compared']:3d} {r['spearman_sim']:8.2f} {r['spearman_grid']:9.2f} "
              f"{r['mae_sim']:8.2f} {r['mae_grid']:9.2f} {r['coverage_p05_p95']:6.0%}")
        print("    biggest misses: " + ", ".join(
            f"{m['driver']} sim {m['simulated_mean']} vs {m['actual']} (grid {m['grid']})"
            for m in r["biggest_misses"]))
    n = len(reports)
    mean = lambda k: sum(r[k] for r in reports) / n
    print(f"\nmean over {n} races: rho sim {mean('spearman_sim'):.2f} (observed strategies only "
          f"{mean('spearman_sim_observed'):.2f}) vs grid {mean('spearman_grid'):.2f}; "
          f"MAE sim {mean('mae_sim'):.2f} (observed only {mean('mae_sim_observed'):.2f}) "
          f"vs grid {mean('mae_grid'):.2f}")
    print("In-sample check: driver pace was fitted on these same races.")
    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=2) + "\n")


if __name__ == "__main__":
    main()

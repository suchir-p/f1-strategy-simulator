"""Build the calibration bundle the backend serves.

Usage:
    python data/bundle/build_bundle.py [--engine engine/build]

For every calibrated 2024 race the bundle holds up to two configs:

  race_fitted  The race's own calibration: driver pace and tires fitted on
               that race, rivals on the strategies they actually ran. This is
               RETROSPECTIVE analysis (it knows how the race went).

  pre_race     Built only from information available before the race:
               - training data: races strictly BEFORE this one (none after);
               - from this weekend: starting grid, qualifying laps, tire
                 nomination, race distance;
               - event-measured track constants (pit loss, safety-car lap
                 time), standing in for the prior-year values teams would have.
                 These are declared in the provenance record;
               - rivals run a reference strategy computed from this pre-race
                 config (the optimizer's pick for the midfield car), never
                 their actual strategies.
               Only offered once MIN_PRIOR_RACES races of history exist.

The output is data/bundle/2024_bundle.json. Its data_version is a hash of its
contents, so any recalibration produces a new version.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data" / "prediction"))
import predict_races as pr  # noqa: E402

cr, cs = pr.cr, pr.cs

SEASON = 2024
DATA_LABEL = "2024"
MIN_PRIOR_RACES = 5
SIGMA_RACE = 0.3  # Hyperparameter from the leave-one-out study (first 15 races).
REFERENCE_SEARCH = {"scenarios": 200, "seed": 7, "lap_step": 3, "min_stint_laps": 8,
                    "max_candidates": 10000}
HEURISTIC_FRACTION = 0.45  # Rivals' placeholder before the reference search: M -> H.
OUT = Path(__file__).resolve().parent / f"{SEASON}_bundle.json"


def race_id(stem: str) -> str:
    return f"{SEASON}-{stem.replace('_', '-')}"


def heuristic_strategy(total_laps: int) -> dict:
    return {"start": "M", "stops": [{"lap": round(total_laps * HEURISTIC_FRACTION), "compound": "H"}]}


def prerace_inputs(stem: str, races: dict[str, dict]) -> tuple[dict, dict]:
    """Pre-race config with every rival on a placeholder strategy, plus its
    provenance. Uses only races before `stem` (chronological)."""
    idx = pr.ORDER.index(stem)
    train = pr.ORDER[:idx]
    if len(train) < MIN_PRIOR_RACES:
        raise ValueError(f"{stem}: only {len(train)} earlier races (need {MIN_PRIOR_RACES})")
    cfg = pr.build_prerace_config(stem, train, races, SIGMA_RACE, use_form=True)
    placeholder = heuristic_strategy(cfg["total_laps"])
    for d in cfg["drivers"]:
        d["strategy"] = copy.deepcopy(placeholder)
        d.pop("strategy_source", None)
    cfg["name"] = races[stem]["cfg"]["name"]
    provenance = {
        "mode": "pre_race",
        "trained_on": [race_id(s) for s in train],
        "from_this_weekend": ["starting grid", "qualifying best laps", "tire nomination",
                              "race distance"],
        "event_measured_track_constants": ["pit_loss_green", "pit_loss_safety_car",
                                           "safety_car.lap_time"],
        "race_pace_sigma": {"value": SIGMA_RACE,
                            "source": "hyperparameter chosen by leave-one-out on the first 15 races"},
    }
    return cfg, provenance


def add_reference_strategy(cfg: dict, provenance: dict, f1sim) -> None:
    """Rivals' default strategy: the optimizer's pick for the car starting
    10th, with the field on the placeholder strategy (one best-response step)."""
    driver = min(cfg["drivers"], key=lambda d: abs(d["grid_position"] - 10))["code"]
    r = REFERENCE_SEARCH
    out = json.loads(f1sim.optimize(json.dumps(cfg), driver, scenarios=r["scenarios"], seed=r["seed"],
                                    lap_step=r["lap_step"], min_stint_laps=r["min_stint_laps"],
                                    max_candidates=r["max_candidates"], top_k=1))
    best = out["ranked"][0]["strategy"]
    strategy = {"start": best["start"], "stops": best["stops"]}
    for d in cfg["drivers"]:
        d["strategy"] = copy.deepcopy(strategy)
        d["strategy_source"] = "reference"
    provenance["rival_strategy"] = {
        "label": best["label"],
        "method": f"optimizer pick for {driver} (grid ~10) with rivals on "
                  f"{heuristic_label(cfg['total_laps'])}; {r['scenarios']} scenarios, seed {r['seed']}",
    }


def heuristic_label(total_laps: int) -> str:
    h = heuristic_strategy(total_laps)
    return h["start"] + "".join(f"-{s['lap']}-{s['compound']}" for s in h["stops"])


def tire_records(cfg: dict) -> list[dict]:
    return [{k: c.get(k) for k in ("designation", "absolute_compound", "confidence", "clean_laps",
                                   "max_stint_laps", "pace_offset", "deg_linear", "deg_quadratic")}
            for c in cfg["compounds"]]


def race_record(stem: str, races: dict[str, dict], f1sim) -> dict:
    race = races[stem]
    rnd, name, nomination, note = next(x for x in cs.RACES if cr.slug(x[1]) == stem)
    fitted = copy.deepcopy(race["cfg"])
    fitted_prov = {
        "mode": "race_fitted",
        "description": "driver pace and tires fitted on this race; pooled compounds use other "
                       "2024 races, including later ones; rivals on their actual strategies",
        "calibration_warnings": fitted.get("calibration", {}).get("warnings", []),
    }
    fitted.pop("calibration", None)
    fitted.pop("synthetic", None)
    calibrations = {"race_fitted": {"config": fitted, "provenance": fitted_prov,
                                    "tires": tire_records(fitted)}}
    try:
        pre, prov = prerace_inputs(stem, races)
        add_reference_strategy(pre, prov, f1sim)
        pre.pop("synthetic", None)
        calibrations["pre_race"] = {"config": pre, "provenance": prov, "tires": tire_records(pre)}
        print(f"  {stem:15s} pre_race ok, rivals on {prov['rival_strategy']['label']}")
    except ValueError as exc:
        print(f"  {stem:15s} no pre_race ({exc})")

    return {
        "id": race_id(stem),
        "season": SEASON,
        "round": rnd,
        "name": race["cfg"]["name"],
        "total_laps": race["cfg"]["total_laps"],
        "tire_nomination": dict(zip(("hard", "medium", "soft"), nomination.split(","))),
        "note": note,
        "drivers": [{"code": d["code"], "grid_position": d["grid_position"]}
                    for d in race["cfg"]["drivers"]],
        "actual_result": race["result"]["classification"],
        "calibrations": calibrations,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default=str(ROOT / "engine" / "build"),
                    help="directory containing the f1sim Python module")
    args = ap.parse_args()
    sys.path.insert(0, args.engine)
    import f1sim

    races = {stem: pr.load(stem) for stem in pr.ORDER}
    records = [race_record(stem, races, f1sim) for stem in pr.ORDER]
    body = json.dumps(records, sort_keys=True).encode()
    bundle = {
        "data_version": f"{DATA_LABEL}-{hashlib.sha256(body).hexdigest()[:12]}",
        "built_with_engine": f1sim.__version__,
        "races": records,
    }
    OUT.write_text(json.dumps(bundle, indent=1) + "\n")
    n_pre = sum("pre_race" in r["calibrations"] for r in records)
    print(f"\nwrote {OUT} ({len(records)} races, {n_pre} with pre_race), "
          f"data_version {bundle['data_version']}")


if __name__ == "__main__":
    main()

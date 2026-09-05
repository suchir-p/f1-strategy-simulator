"""Are strategy recommendations robust to what the model doesn't know?

Monte Carlo spread covers luck *within* the model. It says nothing about
whether the model's parameters are right. This script reruns the strategy
search with the uncertain parameters pushed to plausible extremes and checks
whether the recommendation survives.

Usage:
    python data/robustness/robustness.py [--scenarios 300] [--lap-step 3]

For each case study (race, driver), it builds the PRE-RACE config for that race
(same pipeline as data/prediction/: the race itself is excluded from every
estimate), then runs the optimizer under:

    base                 as estimated
    wear -20% / +20%     all compounds' degradation scaled
    no safety cars       deployment probability 0
    busy safety cars     ~1 deployment per race on average
    pit loss -2 s / +2 s

All runs use the same seed, so they share matched scenarios. The base winner
counts as robust if, in every variant, it is either the best candidate or
statistically tied with it (paired 95% interval includes zero). The strategy
the driver actually ran is ranked in every variant too.

Writes data/robustness/results.json.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data" / "prediction"))
import predict_races as pr  # noqa: E402

CASES = [
    ("italy", "LEC"),         # Won Monza on a medium-hard one-stop.
    ("bahrain", "LEC"),       # Soft-hard-hard two-stop.
    ("hungary", "LEC"),       # Earlier fit relied on thin soft data.
    ("spain", "NOR"),         # Pole, finished second.
    ("abu_dhabi", "NOR"),     # Held-out race; won from pole.
]
SIGMA_RACE = 0.3  # Chosen by leave-one-out in data/prediction/.


def variants(base: dict) -> list[tuple[str, dict]]:
    def scaled_wear(f):
        c = copy.deepcopy(base)
        for comp in c["compounds"]:
            comp["deg_linear"] *= f
            comp["deg_quadratic"] *= f
        return c

    def sc(p):
        c = copy.deepcopy(base)
        c["safety_car"]["start_probability_per_lap"] = p
        return c

    def pit(delta):
        c = copy.deepcopy(base)
        c["pit_loss_green"] += delta
        c["pit_loss_safety_car"] += delta / 2
        return c

    laps = base["total_laps"]
    return [
        ("base", base),
        ("wear -20%", scaled_wear(0.8)),
        ("wear +20%", scaled_wear(1.2)),
        ("no safety cars", sc(0.0)),
        ("busy safety cars", sc(1.0 / (laps - 3))),
        ("pit loss -2 s", pit(-2.0)),
        ("pit loss +2 s", pit(2.0)),
    ]


def label(strategy: dict) -> str:
    return strategy["start"] + "".join(f"-{s['lap']}-{s['compound']}" for s in strategy["stops"])


def optimize(config: dict, driver: str, also: str, args) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(config, fh)
        path = fh.name
    cmd = [args.cli, "optimize", "--config", path, "--driver", driver,
           "--scenarios", str(args.scenarios), "--lap-step", str(args.lap_step),
           "--max-candidates", "5000", "--top", "5000", "--also", also]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    Path(path).unlink()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", type=int, default=300)
    ap.add_argument("--lap-step", type=int, default=3)
    ap.add_argument("--cli", default=str(ROOT / "engine" / "build" / "f1sim_cli"))
    ap.add_argument("--hindsight", action="store_true",
                    help="use each race's own fitted config (tires fitted on the race) "
                         "instead of the pre-race config, to measure what track-specific "
                         "tire knowledge is worth")
    args = ap.parse_args()

    races = {stem: pr.load(stem) for stem in pr.ORDER}
    report = []
    for stem, driver in CASES:
        train = [s for s in pr.TRAIN if s != stem]
        if args.hindsight:
            base = json.loads(json.dumps(races[stem]["cfg"]))
            base["noise"]["race_pace_sigma"] = SIGMA_RACE
            for d in base["drivers"]:
                d["dnf_probability"] = pr.event_rates(train, races)["dnf"]
        else:
            base = pr.build_prerace_config(stem, train, races, SIGMA_RACE, use_form=True)
        actual = label(next(d for d in base["drivers"] if d["code"] == driver)["strategy"])

        rows = []
        base_winner = None
        for name, cfg in variants(base):
            res = optimize(cfg, driver, actual, args)
            ranked = res["ranked"]
            by_label = {r["strategy"]["label"]: r for r in ranked}
            winner = ranked[0]
            if base_winner is None:
                base_winner = winner["strategy"]["label"]
                winner_notes = winner["notes"]
            bw = by_label[base_winner]
            act = by_label[actual]
            rows.append({
                "variant": name,
                "winner": winner["strategy"]["label"],
                "winner_mean": winner["stats"]["mean_position"],
                "base_winner_rank": bw["rank"],
                "base_winner_gap": bw["diff_vs_best"],
                "base_winner_tied_or_best": bw["rank"] == 1 or bw["inconclusive_vs_best"],
                "actual_rank": act["rank"],
                "actual_gap": act["diff_vs_best"],
                "actual_tied_or_best": act["rank"] == 1 or act["inconclusive_vs_best"],
                "candidates": res["candidates_evaluated"],
            })

        robust = all(r["base_winner_tied_or_best"] for r in rows)
        # Robust to the perturbations is not the same as trustworthy: flag
        # recommendations resting on borrowed tires or stints beyond the data.
        shaky = [n for n in winner_notes if "neighboring compound" in n or "longer than" in n]
        race_name = races[stem]["cfg"]["name"]
        print(f"\n== {race_name}, {driver}  (actual strategy {actual}; "
              f"{rows[0]['candidates']} candidates x {args.scenarios} races)")
        verdict = "ROBUST" if robust else "SENSITIVE"
        if shaky:
            verdict += ", but relies on low-confidence tire data"
        print(f"   recommended: {base_winner}  ->  {verdict}")
        for note in winner_notes:
            print(f"   note: {note}")
        print(f"   {'variant':18s} {'winner':16s} {'rec. rank':>9s} {'gap':>6s}  {'actual rank':>11s} {'gap':>6s}")
        for r in rows:
            print(f"   {r['variant']:18s} {r['winner']:16s} {r['base_winner_rank']:9d} "
                  f"{r['base_winner_gap']:6.2f}{'' if r['base_winner_tied_or_best'] else '*'} "
                  f"{r['actual_rank']:11d} {r['actual_gap']:6.2f}{'' if r['actual_tied_or_best'] else '*'}")
        report.append({"race": race_name, "driver": driver, "actual": actual,
                       "recommended": base_winner, "robust": robust,
                       "low_confidence_tires": bool(shaky),
                       "recommended_notes": winner_notes, "variants": rows})

    print("\n* = significantly worse than that variant's best (paired 95% interval excludes zero)")
    out = Path(__file__).resolve().parent / ("results_hindsight.json" if args.hindsight else "results.json")
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

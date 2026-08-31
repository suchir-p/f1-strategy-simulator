"""Out-of-sample race prediction: predict races using only pre-race information.

Usage:
    python data/prediction/predict_races.py [--scenarios 1000] [--cli engine/build/f1sim_cli]

Split (chronological, because cars change through the season):
    training: the first 15 calibrated 2024 races (Bahrain ... Singapore)
    test:     the last 5 (United States, Mexico, Las Vegas, Qatar, Abu Dhabi)

What a prediction for race R may use:
    * pre-race facts about R: starting grid, qualifying laps, tire nomination,
      race distance, and two track constants (pit-lane time loss and safety-car
      lap time) that teams know in advance
    * anything learned from races before R or from the training races
    * the strategies drivers actually ran in R. This makes it a strategy-
      conditional prediction: "given these strategies, who finishes where?"
What it may NOT use: R's race lap times, fitted race pace, or result.

Shared parameters estimated from training races:
    * tire offsets and wear per Pirelli compound (C1..C5), pooled
    * qualifying-gap -> race-pace-gap scaling, optionally with recent form
      (each driver's race pace in their previous 3 races)
    * fuel effect, lap noise, safety-car deployment rate, retirement rate
    * race-day pace variation (sigma_race), chosen by leave-one-out on the
      training races so the predicted spread is honest

Outputs data/prediction/results.json and pre-race configs in
data/prediction/configs/.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data" / "calibration"))
import calibrate_race as cr  # noqa: E402
import calibrate_season as cs  # noqa: E402

OUT = cr.OUT_DIR
PRED_DIR = Path(__file__).resolve().parent
ORDER = [cr.slug(name) for _, name, _, _ in cs.RACES]  # Chronological.
TRAIN = ORDER[:15]
TEST = ORDER[15:]
SIGMAS = [0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0]
EPS = 1e-3  # Probability floor for the log score.


# ---------------------------------------------------------------- loading ----

def load(stem: str) -> dict:
    s = f"2024_{stem}"
    cfg = json.loads((OUT / f"{s}.json").read_text())
    raw, results, meta = cr.load_snapshot(s)
    return {
        "stem": stem,
        "cfg": cfg,
        "result": json.loads((OUT / f"{s}_result.json").read_text()),
        "qual": json.loads((OUT / f"{s}_qualifying.json").read_text()),
        "laps": cr.prepare_laps(raw),
        "meta": meta,
    }


def fitted_drivers(cfg: dict) -> set[str]:
    """Drivers whose race pace was actually fitted (not a fallback value)."""
    bad = {w.split(":")[0] for w in cfg["calibration"]["warnings"] if "too few clean laps" in w}
    return {d["code"] for d in cfg["drivers"]} - bad


def race_gaps(race: dict) -> dict[str, float]:
    """Fitted race pace relative to the field median (seconds)."""
    ok = fitted_drivers(race["cfg"])
    base = {d["code"]: d["base_lap_time"] for d in race["cfg"]["drivers"] if d["code"] in ok}
    m = median(base.values())
    return {k: v - m for k, v in base.items()}


def quali_gaps(race: dict) -> dict[str, float]:
    best = {d["driver"]: d["best_lap"] for d in race["qual"]["drivers"] if d["best_lap"] is not None}
    m = median(best.values())
    gaps = {k: v - m for k, v in best.items()}
    worst = max(gaps.values())
    for d in race["cfg"]["drivers"]:  # No qualifying time: assume the back of the field.
        gaps.setdefault(d["code"], worst + 1.0)
    return gaps


def is_finisher(c: dict) -> bool:
    return c["position"] is not None and (c["status"] in ("Finished", "Lapped") or
                                          str(c["status"]).startswith("+"))


# ------------------------------------------------------- shared estimates ----

def form_gap(code: str, stem: str, races: dict[str, dict],
             allowed: set[str] | None = None) -> float | None:
    """Mean race-pace gap over the driver's previous (up to) 3 races. With
    `allowed`, only those races count (keeps held-out races out of training)."""
    idx = ORDER.index(stem)
    vals = []
    for prev in reversed(ORDER[:idx]):
        if allowed is not None and prev not in allowed:
            continue
        g = race_gaps(races[prev]).get(code)
        if g is not None:
            vals.append(g)
        if len(vals) == 3:
            break
    return float(np.mean(vals)) if vals else None


def fit_pace_model(train: list[str], races: dict[str, dict]) -> dict:
    """Race gap ~ b*quali gap (+ c*form). Least squares without intercept on
    gaps within 4 s of the median (drops broken qualifying laps)."""
    q_only, with_form = [], []
    ratios = []
    for stem in train:
        r = races[stem]
        rg, qg = race_gaps(r), quali_gaps(r)
        for code, g in rg.items():
            if code in qg and abs(qg[code]) <= 4 and abs(g) <= 4:
                q_only.append((qg[code], g))
                f = form_gap(code, stem, races, allowed=set(train))
                if f is not None:
                    with_form.append((qg[code], f, g))
        base = [d["base_lap_time"] for d in r["cfg"]["drivers"] if d["code"] in fitted_drivers(r["cfg"])]
        best = [d["best_lap"] for d in r["qual"]["drivers"] if d["best_lap"] is not None]
        ratios.append(median(base) / median(best))
    q = np.array(q_only)
    b = float(np.linalg.lstsq(q[:, :1], q[:, 1], rcond=None)[0][0])
    wf = np.array(with_form)
    b1, b2 = (float(x) for x in np.linalg.lstsq(wf[:, :2], wf[:, 2], rcond=None)[0])
    return {"b_quali": b, "b1_quali": b1, "b2_form": b2, "level_ratio": float(median(ratios))}


def event_rates(train: list[str], races: dict[str, dict]) -> dict:
    deployments = eligible = 0
    retired = starts = 0
    for stem in train:
        r = races[stem]
        laps = sorted(r["result"]["safety_car_laps"])
        deployments += sum(1 for i, lap in enumerate(laps) if i == 0 or lap != laps[i - 1] + 1)
        eligible += r["cfg"]["total_laps"] - 3
        for c in r["result"]["classification"]:
            starts += 1
            retired += not is_finisher(c)
    return {"sc_per_lap": deployments / eligible, "dnf": retired / starts}


def pooled_scalars(train: list[str], races: dict[str, dict]) -> dict:
    return {
        "fuel": median(races[s]["cfg"]["fuel_effect_per_lap"] for s in train),
        "lap_sigma": median(races[s]["cfg"]["noise"]["lap_sigma"] for s in train),
    }


# ------------------------------------------------------ pre-race configs ----

def build_prerace_config(stem: str, train: list[str], races: dict[str, dict], sigma: float,
                         use_form: bool) -> dict:
    race = races[stem]
    cfg = race["cfg"]
    pace = fit_pace_model(train, races)
    rates = event_rates(train, races)
    scal = pooled_scalars(train, races)

    # Tire nomination for this weekend; every compound comes from the season
    # pool built on training races (pooled, or fallback for an unseen compound).
    nomination = next(c for _, name, c, _ in cs.RACES if cr.slug(name) == stem)
    hard, medium, soft = (x.strip().upper() for x in nomination.split(","))
    absolute = {"HARD": hard, "MEDIUM": medium, "SOFT": soft}
    entries: dict = {}
    cr.apply_tire_pool(entries, absolute, cr.build_tire_pool([races[t]["cfg"] for t in train]), [])
    compounds = [entries[c] for c in cr.DRY]

    qg = quali_gaps(race)
    best = [d["best_lap"] for d in race["qual"]["drivers"] if d["best_lap"] is not None]
    level = pace["level_ratio"] * median(best)
    drivers = []
    for d in cfg["drivers"]:
        code = d["code"]
        f = form_gap(code, stem, races) if use_form else None
        gap = (pace["b1_quali"] * qg[code] + pace["b2_form"] * f) if f is not None \
            else pace["b_quali"] * qg[code]
        drivers.append({
            "code": code,
            "base_lap_time": round(level + gap, 4),
            "grid_position": d["grid_position"],
            "dnf_probability": rates["dnf"],
            "strategy": d["strategy"],
            "strategy_source": d.get("strategy_source", "observed"),
        })

    sc = dict(cfg["safety_car"])
    sc["start_probability_per_lap"] = rates["sc_per_lap"]
    return {
        "name": cfg["name"] + " (pre-race prediction)",
        "season": 2024,
        "synthetic": False,
        "total_laps": cfg["total_laps"],
        "fuel_effect_per_lap": scal["fuel"],
        "pit_loss_green": cfg["pit_loss_green"],
        "pit_loss_safety_car": cfg["pit_loss_safety_car"],
        "grid_spacing": cfg["grid_spacing"],
        "min_compounds_required": 2,
        "compounds": compounds,
        "safety_car": sc,
        "noise": {"race_pace_sigma": sigma, "lap_sigma": scal["lap_sigma"]},
        "drivers": drivers,
    }


# ---------------------------------------------------------------- scoring ----

def spearman(a: list[float], b: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    return 1 - 6 * sum((x - y) ** 2 for x, y in zip(ra, rb)) / (n * (n * n - 1))


def simulate_field(config: dict, cli: str, scenarios: int) -> dict[str, dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(config, fh)
        path = fh.name
    proc = subprocess.run([cli, "field", "--config", path, "--scenarios", str(scenarios)],
                          capture_output=True, text=True)
    Path(path).unlink()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return {d["driver"]: d for d in json.loads(proc.stdout)["drivers"]}


def score(race: dict, sim: dict[str, dict], observed_only: bool = False) -> dict:
    source = {d["code"]: d.get("strategy_source", "observed") for d in race["cfg"]["drivers"]}
    finished = [c for c in race["result"]["classification"] if is_finisher(c) and c["driver"] in sim
                and (not observed_only or source.get(c["driver"]) == "observed")]
    codes = [c["driver"] for c in finished]
    actual = {c["driver"]: c["position"] for c in finished}
    grid = {c["driver"]: c["grid"] or 20 for c in finished}
    qg = quali_gaps(race)
    # Real retirements are excluded, so score each finisher against the
    # simulated distribution *given that they finish* (drop the DNF bin and
    # renormalize). Otherwise every interval's top end is "DNF" and coverage
    # is inflated.
    def finish_dist(c):
        h = np.array(sim[c]["histogram"][:-1])
        return h / h.sum() if h.sum() > 0 else np.full(len(h), 1 / len(h))

    def interval(c):
        cdf = np.cumsum(finish_dist(c))
        return int(np.searchsorted(cdf, 0.05) + 1), int(np.searchsorted(cdf, 0.95) + 1)

    pred = [float(np.dot(finish_dist(c), np.arange(1, len(finish_dist(c)) + 1))) for c in codes]
    act = [actual[c] for c in codes]
    logp = [math.log(max(finish_dist(c)[actual[c] - 1], EPS)) for c in codes]
    return {
        "race": race["cfg"]["name"],
        "drivers": len(codes),
        "spearman": spearman(pred, act),
        "spearman_grid": spearman([grid[c] for c in codes], act),
        "spearman_quali": spearman([qg[c] for c in codes], act),
        "mae": float(np.mean([abs(p - a) for p, a in zip(pred, act)])),
        "mae_grid": float(np.mean([abs(grid[c] - actual[c]) for c in codes])),
        "coverage": float(np.mean([interval(c)[0] <= actual[c] <= interval(c)[1] for c in codes])),
        "log_score": float(np.mean(logp)),
    }


def mean_of(rows: list[dict], key: str) -> float:
    return float(np.mean([r[key] for r in rows]))


# ------------------------------------------------------------------- main ----

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", type=int, default=1000)
    ap.add_argument("--cli", default=str(ROOT / "engine" / "build" / "f1sim_cli"))
    args = ap.parse_args()

    races = {stem: load(stem) for stem in ORDER}
    report: dict = {"train": TRAIN, "test": TEST, "models": {}}

    for model, use_form in (("quali", False), ("quali+form", True)):
        # 1. Leave-one-out on training races to choose sigma_race.
        loo = {}
        for sigma in SIGMAS:
            rows = []
            for held in TRAIN:
                others = [s for s in TRAIN if s != held]
                cfg = build_prerace_config(held, others, races, sigma, use_form)
                rows.append(score(races[held], simulate_field(cfg, args.cli, args.scenarios)))
            loo[sigma] = rows
        best_sigma = max(SIGMAS, key=lambda s: mean_of(loo[s], "log_score"))
        print(f"\n== model: {model}  (leave-one-out over {len(TRAIN)} training races)")
        print("sigma_race  log score  coverage  rho")
        for s in SIGMAS:
            mark = "  <- chosen" if s == best_sigma else ""
            print(f"  {s:5.2f}     {mean_of(loo[s], 'log_score'):7.3f}   "
                  f"{mean_of(loo[s], 'coverage'):6.0%}   {mean_of(loo[s], 'spearman'):.2f}{mark}")

        # 2. Predict the held-out test races with everything fitted on training.
        PRED_DIR.joinpath("configs").mkdir(exist_ok=True)
        test_rows, test_obs = [], []
        for stem in TEST:
            cfg = build_prerace_config(stem, TRAIN, races, best_sigma, use_form)
            if use_form:
                (PRED_DIR / "configs" / f"2024_{stem}_prerace.json").write_text(json.dumps(cfg, indent=2) + "\n")
            sim = simulate_field(cfg, args.cli, args.scenarios)
            test_rows.append(score(races[stem], sim))
            test_obs.append(score(races[stem], sim, observed_only=True))

        print(f"\nheld-out test races (sigma_race = {best_sigma})")
        print(f"{'race':30s} {'rho':>5s} {'grid':>5s} {'quali':>6s} {'MAE':>5s} {'grid':>5s} {'cover':>6s} {'logS':>6s}")
        for r in test_rows:
            print(f"{r['race']:30s} {r['spearman']:5.2f} {r['spearman_grid']:5.2f} {r['spearman_quali']:6.2f} "
                  f"{r['mae']:5.2f} {r['mae_grid']:5.2f} {r['coverage']:6.0%} {r['log_score']:6.2f}")
        print(f"{'mean':30s} {mean_of(test_rows, 'spearman'):5.2f} {mean_of(test_rows, 'spearman_grid'):5.2f} "
              f"{mean_of(test_rows, 'spearman_quali'):6.2f} {mean_of(test_rows, 'mae'):5.2f} "
              f"{mean_of(test_rows, 'mae_grid'):5.2f} {mean_of(test_rows, 'coverage'):6.0%} "
              f"{mean_of(test_rows, 'log_score'):6.2f}")
        print(f"{'mean, observed strategies only':30s} {mean_of(test_obs, 'spearman'):5.2f} "
              f"{mean_of(test_obs, 'spearman_grid'):5.2f} {mean_of(test_obs, 'spearman_quali'):6.2f} "
              f"{mean_of(test_obs, 'mae'):5.2f} {mean_of(test_obs, 'mae_grid'):5.2f} "
              f"{mean_of(test_obs, 'coverage'):6.0%} {mean_of(test_obs, 'log_score'):6.2f}")
        report["models"][model] = {
            "sigma_race": best_sigma,
            "loo": {str(s): rows for s, rows in loo.items()},
            "test": test_rows,
            "test_observed_only": test_obs,
        }

    pool = cr.build_tire_pool([races[t]["cfg"] for t in TRAIN])
    report["shared_estimates"] = {
        "pace_model": fit_pace_model(TRAIN, races),
        "tires": {"wear": {f"C{n}": w for n, w in pool["wear"].items()},
                  "steps_harder_minus_softer": {f"C{n}-C{n + 1}": v for n, v in pool["steps"].items()}},
        "rates": event_rates(TRAIN, races),
        "scalars": pooled_scalars(TRAIN, races),
    }
    (PRED_DIR / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {PRED_DIR / 'results.json'}")
    print("Uniform-guess log score for reference: "
          f"{math.log(1 / 20):.2f} (higher is better)")


if __name__ == "__main__":
    main()

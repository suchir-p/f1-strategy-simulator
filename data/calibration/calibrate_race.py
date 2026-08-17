"""Build a calibrated race config (the engine's JSON format) from FastF1 data.

Usage:
    python data/calibration/calibrate_race.py --season 2024 --race Bahrain \
        --compounds C1,C2,C3

`--compounds` is the weekend's hard,medium,soft nomination in Pirelli
C-numbers. FastF1 only reports SOFT/MEDIUM/HARD, so the absolute compounds must
be supplied (check Pirelli's race preview for the weekend).

Outputs (in data/calibration/output/):
    <season>_<race>.json          race config for the engine
    <season>_<race>_result.json   actual classification, for validation
    <season>_<race>_qualifying.json  each driver's best qualifying lap
    <season>_<race>_laps.csv      the clean laps used in the fit, so the
                                  fit can be rerun without FastF1
                                  (see --from-laps)

Calibration approach (see docs/model.md):
  * Clean laps only: green flag, accurate timing, not in/out laps, not lap 1,
    dry compounds, and within 107% of the driver's median.
  * Bounded least-squares fit of
        lap = T_driver + Delta_compound + alpha_c*age + beta_c*age^2 + gamma*laps_remaining
    with the softest raced compound as the reference (offset 0), constrained so
    wear never makes a tire faster and harder compounds never start faster.
    Tire age resets at each stop, which is what separates degradation from
    the fuel term; gamma also absorbs track evolution.
  * Pit loss = (in-lap + out-lap) - 2 * the driver's clean-lap median,
    median over stops, separately for green and safety-car stops.
  * lap_sigma = residual standard deviation of the fit.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "output"
CACHE_DIR = ROOT / ".fastf1-cache"

DRY = ["SOFT", "MEDIUM", "HARD"]
MIN_COMPOUND_LAPS = 20  # Below this, a compound's wear curve isn't fitted.
RACE_CONFIDENCE_LAPS = 100  # A race fit needs this many clean laps to be trusted.
FALLBACK_WEAR_FACTOR = 1.25  # Borrowed wear is made this much harsher.
FALLBACK_MAX_STINT = 15
SEVERITY_RANGE = (0.5, 3.0)  # Clamp on the track tire-severity factor.
# A race fit is also only trusted if its wear is plausible: after 20 laps a
# dry tire should have lost at least 0.2 s and at most 8 s per lap.
PLAUSIBLE_WEAR_20_LAPS = (0.2, 8.0)
LETTER = {"SOFT": "S", "MEDIUM": "M", "HARD": "H"}

# Defaults for parameters a single race can't pin down well.
DEFAULTS = {
    "race_pace_sigma": 0.15,
    "dnf_probability": 0.05,
    "sc_start_probability_per_lap": 0.02,
    "grid_spacing": 0.25,
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def seconds(td: pd.Series) -> pd.Series:
    return td.dt.total_seconds()


RAW_LAP_COLUMNS = ["Driver", "LapNumber", "Stint", "Compound", "TyreLife", "LapTimeSeconds",
                   "TrackStatus", "IsAccurate", "PitIn", "PitOut"]
RESULT_COLUMNS = ["Abbreviation", "GridPosition", "Position", "ClassifiedPosition", "Status"]


def load_from_fastf1(season: int, race: str):
    """Returns (raw laps, results, metadata) from FastF1."""
    import fastf1  # Imported here so snapshot refits work without FastF1.

    CACHE_DIR.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(CACHE_DIR))
    session = fastf1.get_session(season, race, "R")
    session.load(laps=True, telemetry=False, weather=False, messages=False)
    laps = session.laps
    raw = pd.DataFrame({
        "Driver": laps["Driver"],
        "LapNumber": laps["LapNumber"],
        "Stint": laps["Stint"],
        "Compound": laps["Compound"],
        "TyreLife": laps["TyreLife"],
        "LapTimeSeconds": seconds(laps["LapTime"]),
        "TrackStatus": laps["TrackStatus"],
        "IsAccurate": laps["IsAccurate"],
        "PitIn": laps["PitInTime"].notna(),
        "PitOut": laps["PitOutTime"].notna(),
    })
    results = session.results[RESULT_COLUMNS].copy()
    meta = {"event_name": str(session.event["EventName"]), "total_laps": int(session.total_laps)}
    return raw, results, meta


def qualifying_path(stem: str) -> Path:
    return OUT_DIR / f"{stem}_qualifying.json"


def fetch_qualifying(season: int, race: str | int, stem: str) -> dict:
    """Downloads the Grand Prix qualifying session and saves each driver's best
    lap. Qualifying is known before the race starts, so it's a fair pace input
    for genuinely held-out race predictions."""
    import fastf1

    CACHE_DIR.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(CACHE_DIR))
    session = fastf1.get_session(season, race, "Q")
    session.load(laps=True, telemetry=False, weather=False, messages=False)

    best: dict[str, float] = {}
    # Official Q1/Q2/Q3 times first.
    res = session.results
    for _, row in res.iterrows():
        times = [row[q].total_seconds() for q in ("Q1", "Q2", "Q3")
                 if q in res.columns and pd.notna(row[q])]
        if times:
            best[row["Abbreviation"]] = min(times)
    # Fall back to the fastest timed lap for anyone without an official time.
    laps = session.laps
    lap_best = laps.dropna(subset=["LapTime"]).groupby("Driver")["LapTime"].min()
    for code, t in lap_best.items():
        best.setdefault(code, t.total_seconds())

    drivers = [{"driver": code,
                "position": int(r["Position"]) if pd.notna(r["Position"]) else None,
                "best_lap": round(best[code], 3) if code in best else None}
               for code, r in ((row["Abbreviation"], row) for _, row in res.iterrows())]
    data = {"event_name": str(session.event["EventName"]), "session": "Qualifying", "drivers": drivers}
    OUT_DIR.mkdir(exist_ok=True)
    qualifying_path(stem).write_text(json.dumps(data, indent=2) + "\n")
    timed = sum(d["best_lap"] is not None for d in drivers)
    print(f"wrote {qualifying_path(stem)} ({timed}/{len(drivers)} drivers with a qualifying time)")
    return data


def save_snapshot(stem: str, raw: pd.DataFrame, results: pd.DataFrame, meta: dict) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    raw[RAW_LAP_COLUMNS].to_csv(OUT_DIR / f"{stem}_laps.csv", index=False)
    snapshot = {**meta, "results": json.loads(results.to_json(orient="records"))}
    (OUT_DIR / f"{stem}_session.json").write_text(json.dumps(snapshot, indent=2) + "\n")


def load_snapshot(stem: str):
    raw = pd.read_csv(OUT_DIR / f"{stem}_laps.csv", dtype={"TrackStatus": str})
    snapshot = json.loads((OUT_DIR / f"{stem}_session.json").read_text())
    results = pd.DataFrame(snapshot.pop("results"), columns=RESULT_COLUMNS)
    return raw, results, snapshot


def prepare_laps(raw: pd.DataFrame) -> pd.DataFrame:
    laps = raw.copy()
    laps["t"] = laps["LapTimeSeconds"]
    # TrackStatus is a string of status codes seen during the lap: 1 = green,
    # 4 = safety car (CSV round trips can turn "1" into "1.0").
    laps["status"] = (laps["TrackStatus"].fillna("").astype(str)
                      .str.replace(r"\.0$", "", regex=True))
    laps["green"] = laps["status"].apply(lambda s: s != "" and set(s) <= {"1"})
    laps["sc"] = laps["status"].str.contains("4")
    laps["in_lap"] = laps["PitIn"].astype(bool)
    laps["out_lap"] = laps["PitOut"].astype(bool)
    laps["IsAccurate"] = laps["IsAccurate"].fillna(False).astype(bool)
    laps["age"] = laps["TyreLife"] - 1  # FastF1's TyreLife counts the current lap.
    return laps


def clean_laps(laps: pd.DataFrame) -> pd.DataFrame:
    c = laps[
        laps["green"]
        & laps["IsAccurate"].fillna(False)
        & ~laps["in_lap"]
        & ~laps["out_lap"]
        & (laps["LapNumber"] > 1)
        & laps["Compound"].isin(DRY)
        & laps["t"].notna()
        & laps["age"].notna()
    ].copy()
    median = c.groupby("Driver")["t"].transform("median")
    return c[c["t"] <= 1.07 * median]


def fit_lap_model(clean: pd.DataFrame, total_laps: int):
    drivers = sorted(clean["Driver"].unique())
    # The softest compound present is the reference (offset 0). Usually that's
    # the soft, but e.g. nobody ran a clean soft lap in Baku 2024.
    compounds = [c for c in DRY if c in set(clean["Compound"])]
    if len(compounds) < 2:
        raise ValueError(f"need clean laps on at least two compounds, found {compounds}")
    # Physical constraints, enforced with bounded least squares:
    #   * wear never makes a tire faster:      alpha_c >= 0, beta_c >= 0
    #   * harder compounds start no faster:    Delta_soft = 0 <= Delta_medium <= Delta_hard
    #   * more fuel never makes a car faster:  gamma >= 0
    # The offset ordering uses cumulative "step" columns: the k-th step is 1
    # for every compound at least k steps harder than soft, so each step has
    # a >= 0 bound and the offsets come out ordered.
    rank = {c: i for i, c in enumerate(compounds)}  # 0 = reference compound.
    compound_rank = clean["Compound"].map(rank)

    cols, names, lower = [], [], []
    for d in drivers:
        cols.append((clean["Driver"] == d).astype(float))
        names.append(("driver", d))
        lower.append(-np.inf)
    for k in range(1, len(compounds)):
        cols.append((compound_rank >= k).astype(float))
        names.append(("step", k))
        lower.append(0.0)
    for c in compounds:
        mask = (clean["Compound"] == c).astype(float)
        cols.append(mask * clean["age"])
        names.append(("lin", c))
        lower.append(0.0)
        cols.append(mask * clean["age"] ** 2)
        names.append(("quad", c))
        lower.append(0.0)
    laps_remaining = total_laps - clean["LapNumber"] + 1
    cols.append(laps_remaining.astype(float))
    names.append(("fuel", None))
    lower.append(0.0)

    X = np.column_stack(cols)
    y = clean["t"].to_numpy()
    fit = lsq_linear(X, y, bounds=(np.array(lower), np.full(len(lower), np.inf)), method="bvls")
    coef = fit.x
    residuals = y - X @ coef
    params = {name: value for name, value in zip(names, coef)}

    # Turn the cumulative steps back into per-compound offsets.
    offset = 0.0
    params[("offset", compounds[0])] = 0.0
    for k in range(1, len(compounds)):
        offset += params[("step", k)]
        params[("offset", compounds[k])] = offset

    # Unconstrained fit, kept only to report which constraints were active.
    free, *_ = np.linalg.lstsq(X, y, rcond=None)
    active = [names[i] for i, (lo, f, x) in enumerate(zip(lower, free, coef))
              if lo == 0.0 and f < 0 and abs(x) < 1e-9]
    params[("active_constraints", None)] = active
    return params, drivers, compounds, float(np.std(residuals, ddof=X.shape[1]))


def observed_stint_limits(clean: pd.DataFrame) -> dict[str, int]:
    """Longest tire age (laps) seen in clean data per compound. The optimizer
    won't build stints much longer than this, to avoid extrapolating the wear
    curve far outside the data it was fitted on."""
    return {c: int(g["age"].max()) + 1 for c, g in clean.groupby("Compound")}


def pit_losses(laps: pd.DataFrame, clean: pd.DataFrame) -> tuple[list[float], list[float], list[float]]:
    """Time lost to each pit stop (in-lap + out-lap), split by conditions.

    Green stops are compared with the driver's own median clean lap. Stops
    under the safety car or virtual safety car are compared with the cars on
    the same two laps that didn't stop: everyone is slow on those laps, so the
    driver's green median would count the neutralization itself as pit loss
    (that bug once put the SC pit loss at 60-90 s instead of ~12 s)."""
    median = clean.groupby("Driver")["t"].median()
    running = laps[~laps["in_lap"] & ~laps["out_lap"] & laps["t"].notna()]
    lap_ref = running.groupby("LapNumber")["t"].median()
    status = laps["status"] if "status" in laps else pd.Series("", index=laps.index)
    laps = laps.assign(vsc=status.str.contains("6|7") & ~laps["sc"])
    green, sc, vsc = [], [], []
    by_driver = {d: g.set_index("LapNumber") for d, g in laps.groupby("Driver")}
    for _, lap in laps[laps["in_lap"]].iterrows():
        g = by_driver[lap["Driver"]]
        nxt = lap["LapNumber"] + 1
        if nxt not in g.index:
            continue
        out = g.loc[nxt]
        if pd.isna(lap["t"]) or pd.isna(out["t"]):
            continue
        if lap["sc"] or out["sc"] or lap["vsc"] or out["vsc"]:
            if lap["LapNumber"] not in lap_ref.index or nxt not in lap_ref.index:
                continue
            loss = lap["t"] + out["t"] - lap_ref[lap["LapNumber"]] - lap_ref[nxt]
            (sc if (lap["sc"] or out["sc"]) else vsc).append(loss)
        elif lap["green"] and out["green"] and lap["Driver"] in median:
            green.append(lap["t"] + out["t"] - 2 * median[lap["Driver"]])
    return green, sc, vsc


def driver_strategy(driver_laps: pd.DataFrame) -> dict | None:
    stints = (
        driver_laps.dropna(subset=["Stint"])
        .groupby("Stint")
        .agg(compound=("Compound", "first"), last_lap=("LapNumber", "max"))
        .sort_index()
    )
    if stints.empty or not set(stints["compound"]) <= set(DRY):
        return None
    letters = [LETTER[c] for c in stints["compound"]]
    stops = [{"lap": int(stints["last_lap"].iloc[i]), "compound": letters[i + 1]}
             for i in range(len(stints) - 1)]
    return {"start": letters[0], "stops": stops}


# ---------------------------------------------------- tire confidence levels ----

def build_tire_pool(configs: list[dict]) -> dict:
    """Season-level estimates per Pirelli compound, from race fits with at
    least RACE_CONFIDENCE_LAPS clean laps: median wear, median longest stint,
    and median pace step between adjacent compounds (harder minus softer, from
    races where both were race-fitted)."""
    wear: dict[int, list] = {}
    steps: dict[int, list] = {}
    for cfg in configs:
        fitted = {int(c["absolute_compound"][1:]): c for c in cfg["compounds"]
                  if c.get("confidence", "race") == "race"
                  and c.get("clean_laps", 0) >= RACE_CONFIDENCE_LAPS}
        for n, c in fitted.items():
            wear.setdefault(n, []).append(c)
            if n + 1 in fitted:
                steps.setdefault(n, []).append(c["pace_offset"] - fitted[n + 1]["pace_offset"])
    pool = {"wear": {}, "steps": {}}
    for n, rows in wear.items():
        pool["wear"][n] = {"deg_linear": float(np.median([r["deg_linear"] for r in rows])),
                           "deg_quadratic": float(np.median([r["deg_quadratic"] for r in rows])),
                           "max_stint_laps": int(np.median([r["max_stint_laps"] for r in rows])),
                           "races": len(rows)}
    all_steps = [x for v in steps.values() for x in v]
    for n in range(1, 7):
        pool["steps"][n] = float(np.median(steps[n])) if n in steps else \
            (float(np.median(all_steps)) if all_steps else 0.3)
    return pool


def pooled_wear(n: int, pool: dict) -> tuple[dict, str]:
    """Wear for compound Cn: pooled if seen elsewhere, else a harsher copy of
    the nearest pooled compound."""
    if n in pool["wear"]:
        w = pool["wear"][n]
        return {k: w[k] for k in ("deg_linear", "deg_quadratic", "max_stint_laps")}, "pooled"
    near = min(pool["wear"], key=lambda k: (abs(k - n), -k))
    w = pool["wear"][near]
    return {"deg_linear": w["deg_linear"] * FALLBACK_WEAR_FACTOR,
            "deg_quadratic": w["deg_quadratic"] * FALLBACK_WEAR_FACTOR,
            "max_stint_laps": min(w["max_stint_laps"], FALLBACK_MAX_STINT)}, "fallback"


def apply_tire_pool(entries: dict, absolute: dict, pool: dict, warnings: list) -> None:
    """Replaces low-confidence race fits and fills in nominated compounds that
    weren't fitted, using the season pool. Offsets are placed relative to the
    nearest race-fitted compound with pooled steps, then kept in order."""
    # Track severity: how hard this track is on tires compared with the season,
    # measured on the compounds that WERE race-fitted. Pooled and fallback
    # compounds are scaled by it, so a pooled medium at a high-wear track like
    # Bahrain isn't left looking nearly wear-free next to the fitted ones.
    ratios = []
    for c, e in entries.items():
        n = int(absolute[c][1:])
        if e["confidence"] == "race" and n in pool["wear"]:
            w = pool["wear"][n]
            pooled20 = w["deg_linear"] * 20 + w["deg_quadratic"] * 400
            if pooled20 > 0:
                ratios.append((e["deg_linear"] * 20 + e["deg_quadratic"] * 400) / pooled20)
    severity = float(np.clip(np.median(ratios), *SEVERITY_RANGE)) if ratios else 1.0
    pool_used = False
    for c in DRY:
        if c in entries and entries[c]["confidence"] == "race":
            continue
        pool_used = True
        n = int(absolute[c][1:])
        wear, level = pooled_wear(n, pool)
        wear["deg_linear"] *= severity
        wear["deg_quadratic"] *= severity
        reason = ("low-confidence race fit" if c in entries else "not fitted on this race")
        entries[c] = {"designation": c.lower(), "absolute_compound": absolute[c],
                      "pace_offset": None, **{k: round(v, 6) for k, v in wear.items()},
                      "clean_laps": entries[c]["clean_laps"] if c in entries else 0,
                      "confidence": level}
        entries[c]["max_stint_laps"] = int(entries[c]["max_stint_laps"])
        warnings.append(f"{c}: {reason}; using {level} estimate")
    if pool_used and ratios:
        warnings.append(f"track severity {severity:.2f}x the season median "
                        f"(from {len(ratios)} race-fitted compound(s)); applied to estimated compounds")
    entries["_severity"] = round(severity, 3)
    anchors = [c for c in DRY if entries[c]["confidence"] == "race"]
    if not anchors:  # Nothing race-fitted: lay offsets out from zero.
        entries["SOFT"]["pace_offset"] = 0.0
        anchors = ["SOFT"]
    num = {c: int(absolute[c][1:]) for c in DRY}
    for c in DRY:
        if entries[c]["pace_offset"] is not None:
            continue
        a = min(anchors, key=lambda x: abs(num[x] - num[c]))
        off = entries[a]["pace_offset"]
        lo, hi = sorted((num[c], num[a]))
        delta = sum(pool["steps"][k] for k in range(lo, hi))
        entries[c]["pace_offset"] = round(off + delta if num[c] < num[a] else off - delta, 4)
    # Keep offsets ordered soft <= medium <= hard by adjusting estimated ones only.
    for i in range(1, len(DRY)):
        prev, cur = entries[DRY[i - 1]], entries[DRY[i]]
        if cur["pace_offset"] < prev["pace_offset"]:
            if cur["confidence"] != "race":
                cur["pace_offset"] = prev["pace_offset"]
            elif prev["confidence"] != "race":
                prev["pace_offset"] = cur["pace_offset"]


def reconstruct_strategy(driver_laps: pd.DataFrame, available: set[str], total_laps: int,
                         compounds_soft_to_hard: list[str]) -> tuple[dict, str, str]:
    """Returns (strategy, source, reason). Sources:
      observed      - the stints in the data form a valid strategy
      reconstructed - the observed stints are valid but incomplete (e.g. the
                      driver retired before a second compound); a final stop
                      onto a different compound is added after the last lap
      fallback      - no usable stint data; softest-to-hardest at half distance
    """
    s = driver_strategy(driver_laps)
    if s is not None:
        used = [s["start"]] + [x["compound"] for x in s["stops"]]
        laps_ok = all(x["lap"] < total_laps for x in s["stops"])
        if set(used) <= available and laps_ok:
            if len(set(used)) >= 2:
                return s, "observed", ""
            last_stop = s["stops"][-1]["lap"] if s["stops"] else 0
            last_lap = int(driver_laps["LapNumber"].max()) if len(driver_laps) else 1
            lap = min(max(last_lap, last_stop + 1), total_laps - 1)
            other = next((LETTER[c] for c in compounds_soft_to_hard if LETTER[c] != used[-1]), None)
            if other is not None and lap > last_stop:
                s = {"start": s["start"], "stops": s["stops"] + [{"lap": lap, "compound": other}]}
                return s, "reconstructed", f"one compound in {last_lap} laps; added a stop on lap {lap}"
    first = LETTER[compounds_soft_to_hard[0]]
    last = LETTER[compounds_soft_to_hard[-1]]
    why = "no usable stint data" if s is None else "stints don't form a usable strategy"
    return {"start": first, "stops": [{"lap": total_laps // 2, "compound": last}]}, "fallback", why


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--race", required=True, help="event name, e.g. Bahrain")
    ap.add_argument("--compounds", required=True, help="hard,medium,soft as C-numbers, e.g. C1,C2,C3")
    ap.add_argument("--from-snapshot", action="store_true",
                    help="refit from the saved _laps.csv/_session.json instead of FastF1")
    args = ap.parse_args()
    calibrate(args.season, args.race, args.compounds, from_snapshot=args.from_snapshot,
              fetch_missing_qualifying=not args.from_snapshot)


def calibrate(season: int, race: str | int, compounds_arg: str, *, name: str | None = None,
              from_snapshot: bool = False, fetch_missing_qualifying: bool = True,
              tire_pool: dict | None = None) -> dict:
    """Calibrates one race and writes its files. `race` is an event name or a
    round number; `name` (default: `race`) sets the output file names.
    Returns a short summary."""
    hard, medium, soft = [c.strip().upper() for c in compounds_arg.split(",")]
    absolute = {"HARD": hard, "MEDIUM": medium, "SOFT": soft}

    stem = f"{season}_{slug(str(name if name is not None else race))}"
    if from_snapshot:
        raw, results, meta = load_snapshot(stem)
    else:
        raw, results, meta = load_from_fastf1(season, race)
        save_snapshot(stem, raw, results, meta)
    if fetch_missing_qualifying and not qualifying_path(stem).exists():
        try:
            fetch_qualifying(season, race, stem)
        except Exception as exc:  # Qualifying is optional for calibration.
            print(f"warning: could not fetch qualifying ({exc}); rerun later to retry")
    total_laps = int(meta["total_laps"])
    laps = prepare_laps(raw)
    clean = clean_laps(laps)
    # Compounds with very few clean laps can't support a wear curve: leave them
    # out of the fit and the config (drivers who used them get a fallback
    # strategy, flagged below).
    lap_counts = clean["Compound"].value_counts()
    thin = sorted((c for c in lap_counts.index if lap_counts[c] < MIN_COMPOUND_LAPS), key=DRY.index)
    clean = clean[~clean["Compound"].isin(thin)]
    params, fitted_drivers, compounds, lap_sigma = fit_lap_model(clean, total_laps)
    stint_limits = observed_stint_limits(clean)

    green, sc, _vsc = pit_losses(laps, clean)
    pit_green = float(np.median(green)) if green else 22.0
    pit_sc = float(np.median(sc)) if sc else 0.5 * pit_green

    sc_laps = laps[laps["sc"] & laps["t"].notna()]["t"]
    clean_median = float(clean["t"].median())
    sc_lap_time = float(sc_laps.median()) if len(sc_laps) else 1.4 * clean_median

    base_times = {d: params[("driver", d)] for d in fitted_drivers}
    fallback_base = float(np.median(list(base_times.values()))) + 1.0

    warnings = []
    for c in thin:
        warnings.append(f"{c}: only {int(lap_counts[c])} clean laps (< {MIN_COMPOUND_LAPS}); "
                        "not fitted on this race")
    for pname, compound in params[("active_constraints", None)]:
        label = compound if compound is not None else ""
        warnings.append(f"constraint active: {pname} {label} pinned at 0 "
                        "(the unconstrained fit wanted a physically impossible value)")

    # ---- compounds: race fit, pooled estimate, or conservative fallback ----
    entries = {}
    for c in compounds:
        n_laps = int((clean["Compound"] == c).sum())
        wear20 = params[("lin", c)] * 20 + params[("quad", c)] * 400
        plausible = PLAUSIBLE_WEAR_20_LAPS[0] <= wear20 <= PLAUSIBLE_WEAR_20_LAPS[1]
        if n_laps >= RACE_CONFIDENCE_LAPS and not plausible:
            warnings.append(f"{c}: fitted wear after 20 laps is {wear20:.2f} s, outside "
                            f"{PLAUSIBLE_WEAR_20_LAPS}; treated as low confidence")
        entries[c] = {
            "designation": c.lower(),
            "absolute_compound": absolute[c],
            "pace_offset": round(float(params[("offset", c)]), 4),
            "deg_linear": round(float(params[("lin", c)]), 5),
            "deg_quadratic": round(float(params[("quad", c)]), 6),
            "max_stint_laps": stint_limits.get(c, 0),
            "clean_laps": n_laps,
            "confidence": "race" if n_laps >= RACE_CONFIDENCE_LAPS and plausible else "race_low",
        }
    if tire_pool is not None:
        apply_tire_pool(entries, absolute, tire_pool, warnings)
    else:
        for c, e in entries.items():
            if e["confidence"] == "race_low":
                warnings.append(f"{c}: only {e['clean_laps']} clean laps; its parameters are uncertain")
    severity = entries.pop("_severity", None)
    compound_entries = [entries[c] for c in DRY if c in entries]
    available = {LETTER[c] for c in entries}

    drivers = []
    n = len(results)
    sources = {"observed": 0, "reconstructed": 0, "fallback": 0}
    for _, row in results.iterrows():
        code = row["Abbreviation"]
        grid = int(row["GridPosition"]) if row["GridPosition"] and row["GridPosition"] > 0 else None
        strategy, source, why = reconstruct_strategy(laps[laps["Driver"] == code], available,
                                                     total_laps, [c for c in DRY if c in entries])
        sources[source] += 1
        if source != "observed":
            warnings.append(f"{code}: {source} strategy ({why})")
        if code not in base_times:
            warnings.append(f"{code}: too few clean laps; base pace set to field median + 1.0 s")
        drivers.append({
            "code": code,
            "base_lap_time": round(float(base_times.get(code, fallback_base)), 4),
            "grid_position": grid,
            "dnf_probability": DEFAULTS["dnf_probability"],
            "strategy": strategy,
            "strategy_source": source,
        })
    # Pit-lane starters (grid 0) go to the back, in classification order.
    taken = {d["grid_position"] for d in drivers if d["grid_position"]}
    free = iter(p for p in range(1, n + 1) if p not in taken)
    for d in drivers:
        if d["grid_position"] is None:
            d["grid_position"] = next(free)

    race_cfg = {
        "name": f"{season} {meta['event_name']}",
        "season": season,
        "synthetic": False,
        "total_laps": total_laps,
        "fuel_effect_per_lap": round(float(params[("fuel", None)]), 5),
        "pit_loss_green": round(pit_green, 3),
        "pit_loss_safety_car": round(pit_sc, 3),
        "grid_spacing": DEFAULTS["grid_spacing"],
        "min_compounds_required": 2,
        "compounds": compound_entries,
        "safety_car": {
            "start_probability_per_lap": DEFAULTS["sc_start_probability_per_lap"],
            "min_duration_laps": 2,
            "max_duration_laps": 5,
            "lap_time": round(sc_lap_time, 3),
            "bunched_gap": 1.0,
            "earliest_lap": 2,
            "latest_lap_margin": 2,
        },
        "noise": {"race_pace_sigma": DEFAULTS["race_pace_sigma"], "lap_sigma": round(lap_sigma, 4)},
        "drivers": drivers,
        "calibration": {
            "clean_laps": int(len(clean)),
            "green_pit_stops_measured": len(green),
            "sc_pit_stops_measured": len(sc),
            "strategy_sources": sources,
            "track_severity": severity,
            "warnings": warnings,
        },
    }

    result = {
        "race": race_cfg["name"],
        "classification": [
            {"driver": r["Abbreviation"], "position": int(r["Position"]) if pd.notna(r["Position"]) else None,
             "classified": str(r["ClassifiedPosition"]), "status": str(r["Status"]),
             "grid": int(r["GridPosition"]) if pd.notna(r["GridPosition"]) else None}
            for _, r in results.iterrows()
        ],
        "safety_car_laps": sorted(int(x) for x in laps[laps["sc"]]["LapNumber"].unique()),
    }

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{stem}.json").write_text(json.dumps(race_cfg, indent=2) + "\n")
    (OUT_DIR / f"{stem}_result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {OUT_DIR / (stem + '.json')} ({len(clean)} clean laps, lap_sigma={lap_sigma:.3f})")
    for w in warnings:
        print("warning:", w)
    return {"stem": stem, "clean_laps": int(len(clean)), "lap_sigma": lap_sigma,
            "strategy_sources": sources,
            "tire_confidence": {e["designation"]: e["confidence"] for e in compound_entries},
            "safety_car_laps": len(result["safety_car_laps"]), "warnings": warnings}


if __name__ == "__main__":
    main()

"""Calibrate every usable dry race of the 2024 season in one go.

Usage:
    python data/calibration/calibrate_season.py            # download + fit all races
    python data/calibration/calibrate_season.py --refit    # refit from saved snapshots only

Each race also downloads its qualifying session (best lap per driver), used
later for held-out predictions where pace must come from before the race.

Races are looked up by round number, so FastF1 can't match the wrong event.
Races that already have a snapshot are refitted from it instead of being
downloaded again, so if the run is interrupted, just start it again.
A failure in one race is reported and the run moves on to the next.

Tire nominations are from Pirelli's pre-race announcements (hard, medium,
soft). Excluded rounds:
    8  Monaco   lap-1 red flag gave everyone a free tire change; no real strategy
    9  Canada   mixed wet/dry
    12 Britain  mixed wet/dry
    21 Brazil   wet
Japan (round 4) is included but flagged: a lap-1 red flag allowed a free tire
change, so starting compounds reflect the restart, not the grid.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback

import calibrate_race as cr

SEASON = 2024

# round, name (used for file names), hard,medium,soft, note
RACES = [
    (1, "Bahrain", "C1,C2,C3", ""),
    (2, "Saudi Arabia", "C2,C3,C4", ""),
    (3, "Australia", "C3,C4,C5", ""),
    (4, "Japan", "C1,C2,C3", "lap-1 red flag: free tire change at the restart"),
    (5, "China", "C2,C3,C4", ""),
    (6, "Miami", "C2,C3,C4", ""),
    (7, "Emilia Romagna", "C3,C4,C5", ""),
    (10, "Spain", "C1,C2,C3", ""),
    (11, "Austria", "C3,C4,C5", ""),
    (13, "Hungary", "C3,C4,C5", ""),
    (14, "Belgium", "C2,C3,C4", ""),
    (15, "Netherlands", "C1,C2,C3", ""),
    (16, "Italy", "C3,C4,C5", ""),
    (17, "Azerbaijan", "C3,C4,C5", ""),
    (18, "Singapore", "C3,C4,C5", ""),
    (19, "United States", "C2,C3,C4", ""),
    (20, "Mexico", "C3,C4,C5", ""),
    (22, "Las Vegas", "C3,C4,C5", ""),
    (23, "Qatar", "C1,C2,C3", ""),
    (24, "Abu Dhabi", "C3,C4,C5", ""),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refit", action="store_true", help="only refit races that have snapshots")
    ap.add_argument("--only", help="comma-separated race names to run, e.g. 'Japan,Qatar'")
    args = ap.parse_args()

    wanted = {n.strip().lower() for n in args.only.split(",")} if args.only else None
    summary = []
    start = time.time()
    for i, (rnd, name, compounds, note) in enumerate(RACES, 1):
        if wanted and name.lower() not in wanted:
            continue
        stem = f"{SEASON}_{cr.slug(name)}"
        have_snapshot = (cr.OUT_DIR / f"{stem}_laps.csv").exists() and \
                        (cr.OUT_DIR / f"{stem}_session.json").exists()
        if args.refit and not have_snapshot:
            summary.append({"race": name, "status": "skipped (no snapshot)"})
            continue
        print(f"\n[{i}/{len(RACES)}] {name} (round {rnd}, {compounds})"
              + (" - refitting from snapshot" if have_snapshot else " - downloading"))
        try:
            res = cr.calibrate(SEASON, rnd, compounds, name=name, from_snapshot=have_snapshot,
                               fetch_missing_qualifying=not args.refit)
            res["qualifying"] = cr.qualifying_path(stem).exists()
            summary.append({"race": name, "status": "ok", "note": note, **res})
        except (Exception, SystemExit) as exc:  # Keep going; report at the end.
            traceback.print_exc()
            summary.append({"race": name, "status": f"FAILED: {exc}"})

    # Second pass: refit each race with a tire pool built from the OTHER races'
    # first-pass fits, so thin or missing compounds get pooled estimates.
    ok = [x for x in summary if x["status"] == "ok"]
    first_pass = {x["stem"]: json.loads((cr.OUT_DIR / f"{x['stem']}.json").read_text()) for x in ok}
    if len(first_pass) >= 2:
        print(f"\nSecond pass: pooling tire estimates across {len(first_pass)} races")
        by_name = {name: (rnd, compounds) for rnd, name, compounds, _ in RACES}
        for x in ok:
            rnd, compounds = by_name[x["race"]]
            pool = cr.build_tire_pool([c for stem, c in first_pass.items() if stem != x["stem"]])
            res = cr.calibrate(SEASON, rnd, compounds, name=x["race"], from_snapshot=True,
                               fetch_missing_qualifying=False, tire_pool=pool)
            x.update(res)

    print(f"\n{'=' * 70}\nDone in {(time.time() - start) / 60:.1f} min\n")
    for s in summary:
        line = f"{s['race']:16s} {s['status']}"
        if s["status"] == "ok":
            line += f"  ({s['clean_laps']} clean laps, {len(s['warnings'])} warnings"
            line += ", qualifying ok)" if s.get("qualifying") else ", NO qualifying)"
            src = s.get("strategy_sources", {})
            if src.get("reconstructed") or src.get("fallback"):
                line += f"  strategies: {src.get('reconstructed', 0)} reconstructed, {src.get('fallback', 0)} fallback"
            if s.get("note"):
                line += f"  NOTE: {s['note']}"
        print(line)
    (cr.OUT_DIR / f"{SEASON}_season_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nSummary saved to {cr.OUT_DIR / f'{SEASON}_season_summary.json'}")


if __name__ == "__main__":
    main()

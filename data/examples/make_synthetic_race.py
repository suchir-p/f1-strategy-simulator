"""Generate data/examples/synthetic_race.json.

This is a SYNTHETIC race used for engine development, tests and benchmarks.
Its parameters are hand-picked to be roughly F1-like; they are NOT calibrated
from real data. Calibrated configs come from data/calibration/ once the
FastF1 pipeline has been run.
"""

import json
from pathlib import Path

CODES = ["VER", "NOR", "LEC", "PIA", "SAI", "HAM", "RUS", "PER", "ALO", "STR",
         "GAS", "OCO", "ALB", "TSU", "RIC", "HUL", "MAG", "BOT", "ZHO", "SAR"]

# A few common default strategies, cycled across the grid.
STRATEGIES = [
    {"start": "M", "stops": [{"lap": 20, "compound": "H"}]},
    {"start": "S", "stops": [{"lap": 14, "compound": "H"}]},
    {"start": "H", "stops": [{"lap": 30, "compound": "M"}]},
    {"start": "S", "stops": [{"lap": 13, "compound": "M"}, {"lap": 32, "compound": "H"}]},
]


def main() -> None:
    drivers = []
    for i, code in enumerate(CODES):
        drivers.append({
            "code": code,
            # Front-runners ~0.06 s/lap apart on average, with some spread.
            "base_lap_time": round(80.0 + 0.065 * i + (0.05 if i % 3 == 0 else 0.0), 3),
            "grid_position": i + 1,
            "dnf_probability": 0.05,
            "strategy": STRATEGIES[i % len(STRATEGIES)],
        })

    race = {
        "name": "Synthetic 50-lap test race",
        "season": 2024,
        "synthetic": True,
        "total_laps": 50,
        "fuel_effect_per_lap": 0.03,
        "pit_loss_green": 22.0,
        "pit_loss_safety_car": 11.0,
        "grid_spacing": 0.25,
        "min_compounds_required": 2,
        "compounds": [
            {"designation": "soft", "absolute_compound": "C4",
             "pace_offset": 0.0, "deg_linear": 0.08, "deg_quadratic": 0.0020},
            {"designation": "medium", "absolute_compound": "C3",
             "pace_offset": 0.45, "deg_linear": 0.05, "deg_quadratic": 0.0008},
            {"designation": "hard", "absolute_compound": "C2",
             "pace_offset": 0.90, "deg_linear": 0.03, "deg_quadratic": 0.0004},
        ],
        "safety_car": {
            "start_probability_per_lap": 0.025,
            "min_duration_laps": 2,
            "max_duration_laps": 5,
            "lap_time": 115.0,
            "bunched_gap": 1.0,
            "earliest_lap": 2,
            "latest_lap_margin": 2,
        },
        "interactions": {
            "min_gap": 0.3,
            "dirty_air_gap": 1.0,
            "dirty_air_penalty": 0.25,
            "overtake_pace_threshold": 0.4,
            "overtake_base_probability": 0.25,
            "overtake_probability_per_second": 0.35,
            "max_overtake_probability": 0.9,
        },
        "noise": {"race_pace_sigma": 0.15, "lap_sigma": 0.35},
        "drivers": drivers,
    }

    out = Path(__file__).with_name("synthetic_race.json")
    out.write_text(json.dumps(race, indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

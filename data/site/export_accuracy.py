"""Exports the validation results the dashboard shows in "How accurate is it?".

Reads the outputs of validation/validate_race.py and prediction/predict_races.py
and writes frontend/lib/accuracy.json. Rerun after either changes:

    python data/site/export_accuracy.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

DATA = Path(__file__).resolve().parents[1]
OUT = DATA.parent / "frontend" / "lib" / "accuracy.json"
MODEL = "quali+form"


def short(name: str) -> str:
    return name.replace("2024 ", "").replace(" Grand Prix", "")


def main() -> None:
    in_sample = json.loads((DATA / "validation" / "2024_in_sample.json").read_text())
    pred = json.loads((DATA / "prediction" / "results.json").read_text())
    model = pred["models"][MODEL]
    test = model["test"]
    loo = model["loo"][str(model["sigma_race"])]

    def block(rows, sim_rho, grid_rho, sim_mae, grid_mae, quali_rho=None):
        out = {
            "races": len(rows),
            "spearman": statistics.fmean(r[sim_rho] for r in rows),
            "spearman_grid": statistics.fmean(r[grid_rho] for r in rows),
            "mae": statistics.fmean(r[sim_mae] for r in rows),
            "mae_grid": statistics.fmean(r[grid_mae] for r in rows),
            "beats_grid": sum(r[sim_mae] < r[grid_mae] for r in rows),
            "per_race": [{"race": short(r["race"]), "mae": round(r[sim_mae], 3),
                          "mae_grid": round(r[grid_mae], 3),
                          "spearman": round(r[sim_rho], 3),
                          "spearman_grid": round(r[grid_rho], 3)} for r in rows],
        }
        if quali_rho:
            out["spearman_quali"] = statistics.fmean(r[quali_rho] for r in rows)
        return out

    held_out = block(test, "spearman", "spearman_grid", "mae", "mae_grid", "spearman_quali")
    # Coverage pooled over drivers: share of real finishes inside the 5th-95th
    # percentile range of the predicted (finish-conditional) distribution.
    held_out["coverage"] = (sum(r["coverage"] * r["drivers"] for r in test)
                            / sum(r["drivers"] for r in test))
    held_out["log_score"] = statistics.fmean(r["log_score"] for r in test)
    leave_one_out = block(loo, "spearman", "spearman_grid", "mae", "mae_grid", "spearman_quali")
    insample = block(in_sample, "spearman_sim", "spearman_grid", "mae_sim", "mae_grid")
    # The README reports the in-sample rank correlation as a median.
    insample["spearman_median"] = statistics.median(r["spearman_sim"] for r in in_sample)
    insample["spearman_grid_median"] = statistics.median(r["spearman_grid"] for r in in_sample)

    out = {"season": 2024, "model": MODEL, "train_races": len(pred["train"]),
           "held_out": held_out, "leave_one_out": leave_one_out, "in_sample": insample}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {OUT}")
    for k in ("held_out", "leave_one_out", "in_sample"):
        b = out[k]
        print(f"{k:14s} rho {b['spearman']:.3f} vs grid {b['spearman_grid']:.3f}; "
              f"MAE {b['mae']:.2f} vs {b['mae_grid']:.2f}; beats grid {b['beats_grid']}/{b['races']}")
    print(f"held-out coverage {held_out['coverage']:.3f}, in-sample median rho "
          f"{insample['spearman_median']:.3f} vs {insample['spearman_grid_median']:.3f}")


if __name__ == "__main__":
    main()

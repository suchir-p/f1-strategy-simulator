"""Safety-car and VSC laps read from the real 2024 lap snapshots, which the
in-sample validation replays. Run: pytest data/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data" / "validation"))
import validate_race as vr  # noqa: E402


def laps(stem: str) -> dict[str, list[int]]:
    import json
    raw, _, _ = vr.cr.load_snapshot(stem)
    session = json.loads((vr.OUTPUT / f"{stem}_session.json").read_text())
    winner = next(r["Abbreviation"] for r in session["results"] if r["Position"] == 1)
    return vr.neutralized_laps(raw, winner)


def test_real_2024_events():
    saudi = laps("2024_saudi_arabia")
    assert saudi["sc"] == [7, 8, 9] and saudi["vsc"] == []
    # China: a one-lap VSC upgraded to a safety car is counted as the SC only.
    china = laps("2024_china")
    assert china["sc"] == list(range(23, 32)) and china["vsc"] == []
    assert laps("2024_australia") == {"sc": [], "vsc": [17, 18, 58]}
    assert vr.real_vsc_laps("2024_australia") == [17, 18, 58]
    assert laps("2024_mexico")["sc"][0] == 1  # Lap-1 safety car.

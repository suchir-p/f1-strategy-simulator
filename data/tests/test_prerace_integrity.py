"""A pre-race config must not depend on how its race (or any later race) went.

Each test scrambles race outcomes (lap times, fitted pace and tires, strategies
actually run, the result, safety cars) and rebuilds the pre-race config. The
only fields allowed to change are the declared event-measured track constants.

Run: pytest data/tests
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data" / "bundle"))
import build_bundle as bb  # noqa: E402

pr = bb.pr
DECLARED = {"pit_loss_green", "pit_loss_safety_car", "safety_car.lap_time"}


@pytest.fixture(scope="module")
def races():
    return {stem: pr.load(stem) for stem in pr.ORDER}


def scramble_outcome(races: dict, stem: str) -> dict:
    out = copy.deepcopy(races)
    race = out[stem]
    cfg = race["cfg"]
    for i, d in enumerate(cfg["drivers"]):
        d["base_lap_time"] += 0.37 * (i % 5) - 0.6
        d["strategy"] = {"start": "H", "stops": [{"lap": 9, "compound": "S"}]}
    for c in cfg["compounds"]:
        c["deg_linear"] *= 3
        c["pace_offset"] += 1.0
    cfg["fuel_effect_per_lap"] *= 2
    cfg["noise"]["lap_sigma"] *= 2
    cfg["pit_loss_green"] += 5.0          # Declared track constants:
    cfg["pit_loss_safety_car"] += 3.0     # allowed to change.
    cfg["safety_car"]["lap_time"] += 10.0
    race["result"]["classification"] = list(reversed(race["result"]["classification"]))
    race["result"]["safety_car_laps"] = list(range(1, 11))
    race["laps"]["t"] = race["laps"]["t"] + 3.0
    return out


def diff_paths(a, b, prefix: str = "") -> set[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        paths = set()
        for k in set(a) | set(b):
            p = f"{prefix}.{k}" if prefix else str(k)
            if k not in a or k not in b:
                paths.add(p)
            else:
                paths |= diff_paths(a[k], b[k], p)
        return paths
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return {prefix}
        paths = set()
        for x, y in zip(a, b):
            paths |= diff_paths(x, y, prefix)
        return paths
    return set() if a == b else {prefix}


@pytest.mark.parametrize("stem", ["miami", "italy", "abu_dhabi"])
def test_own_race_outcome_does_not_leak(races, stem):
    base, _ = bb.prerace_inputs(stem, races)
    scrambled, _ = bb.prerace_inputs(stem, scramble_outcome(races, stem))
    assert diff_paths(base, scrambled) <= DECLARED


@pytest.mark.parametrize("stem", ["miami", "italy"])
def test_later_races_do_not_leak(races, stem):
    base, _ = bb.prerace_inputs(stem, races)
    later = pr.ORDER[pr.ORDER.index(stem) + 1:]
    scrambled = races
    for s in later:
        scrambled = scramble_outcome(scrambled, s)
        scrambled[s]["cfg"]["compounds"][0]["confidence"] = "race"
    after, _ = bb.prerace_inputs(stem, scrambled)
    assert diff_paths(base, after) == set()


def test_rivals_never_use_actual_strategies(races):
    cfg, _ = bb.prerace_inputs("italy", races)
    strategies = {str(d["strategy"]) for d in cfg["drivers"]}
    assert strategies == {str(bb.heuristic_strategy(cfg["total_laps"]))}


def test_training_is_strictly_earlier(races):
    for stem in pr.ORDER:
        idx = pr.ORDER.index(stem)
        if idx < bb.MIN_PRIOR_RACES:
            with pytest.raises(ValueError):
                bb.prerace_inputs(stem, races)
            continue
        _, prov = bb.prerace_inputs(stem, races)
        assert prov["trained_on"] == [bb.race_id(s) for s in pr.ORDER[:idx]]

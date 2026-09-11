from __future__ import annotations

from typing import Literal

Mode = Literal["pre_race", "race_fitted"]

MODE_LABELS: dict[str, str] = {
    "pre_race": "Pre-race recommendation",
    "race_fitted": "Retrospective analysis",
}

STRATEGY_PATTERN = r"^[SMH](-\d{1,2}-[SMH]){1,3}$"
DRIVER_PATTERN = r"^[A-Z]{3}$"
MAX_SEED = 2**53 - 1  # Seeds stay exact in JavaScript clients.

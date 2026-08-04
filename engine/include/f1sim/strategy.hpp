#pragma once
// Strategy validation, formatting, and the stint view of a strategy.

#include <string>
#include <vector>

#include "f1sim/types.hpp"

namespace f1sim {

struct Stint {
  int compound_index = 0;
  int start_lap = 1;  // First lap run on this set.
  int end_lap = 1;    // Last lap run on this set (inclusive).
  int length() const { return end_lap - start_lap + 1; }
};

struct ValidationResult {
  bool ok = true;
  std::string error;  // Empty when ok.
};

// Checks pit laps are in range and strictly increasing, compound indices
// exist, and (for dry races) at least `min_compounds_required` distinct
// compounds are used. `min_stint_laps` rejects absurdly short stints.
ValidationResult validate_strategy(const Strategy& strategy, const RaceConfig& race,
                                   int min_stint_laps = 1);

// Validates the whole race configuration, including every driver's strategy.
ValidationResult validate_race(const RaceConfig& race);

std::vector<Stint> to_stints(const Strategy& strategy, int total_laps);

// Human-readable form, e.g. "M-18-H" or "S-12-M-35-H".
std::string format_strategy(const Strategy& strategy, const RaceConfig& race);

char designation_letter(Designation d);

std::string confidence_name(TireConfidence c);  // "race", "race_low", "pooled", "fallback".

// Human-readable caveats for a strategy: stints on tires whose parameters are
// not race-fitted, or stints longer than the longest one seen in the data.
std::vector<std::string> strategy_notes(const Strategy& strategy, const RaceConfig& race);

}  // namespace f1sim

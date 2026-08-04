#include "f1sim/strategy.hpp"

#include <set>
#include <sstream>

namespace f1sim {

char designation_letter(Designation d) {
  switch (d) {
    case Designation::Soft: return 'S';
    case Designation::Medium: return 'M';
    case Designation::Hard: return 'H';
  }
  return '?';
}

std::vector<Stint> to_stints(const Strategy& strategy, int total_laps) {
  std::vector<Stint> stints;
  int start = 1;
  int compound = strategy.start_compound_index;
  for (const auto& stop : strategy.stops) {
    stints.push_back({compound, start, stop.lap});
    start = stop.lap + 1;
    compound = stop.compound_index;
  }
  stints.push_back({compound, start, total_laps});
  return stints;
}

ValidationResult validate_strategy(const Strategy& strategy, const RaceConfig& race,
                                   int min_stint_laps) {
  const int n_compounds = static_cast<int>(race.compounds.size());
  auto bad = [](std::string msg) { return ValidationResult{false, std::move(msg)}; };

  if (strategy.start_compound_index < 0 || strategy.start_compound_index >= n_compounds) {
    return bad("start compound index out of range");
  }
  int previous_lap = 0;
  for (const auto& stop : strategy.stops) {
    if (stop.compound_index < 0 || stop.compound_index >= n_compounds) {
      return bad("pit stop compound index out of range");
    }
    if (stop.lap <= previous_lap) {
      return bad("pit stop laps must be strictly increasing and >= 1");
    }
    if (stop.lap >= race.total_laps) {
      return bad("pit stop lap must be before the final lap");
    }
    previous_lap = stop.lap;
  }

  std::set<int> used;
  for (const auto& stint : to_stints(strategy, race.total_laps)) {
    if (stint.length() < min_stint_laps) {
      return bad("stint shorter than the minimum stint length");
    }
    used.insert(race.compounds[stint.compound_index].absolute_compound);
  }
  if (static_cast<int>(used.size()) < race.min_compounds_required) {
    return bad("strategy must use at least " + std::to_string(race.min_compounds_required) +
               " different dry compounds");
  }
  return {};
}

ValidationResult validate_race(const RaceConfig& race) {
  auto bad = [](std::string msg) { return ValidationResult{false, std::move(msg)}; };
  if (race.total_laps < 2) return bad("race must have at least 2 laps");
  if (race.compounds.empty()) return bad("race must define at least one compound");
  if (race.drivers.empty()) return bad("race must have at least one driver");
  if (race.pit_loss_green < 0 || race.pit_loss_safety_car < 0) {
    return bad("pit losses must be non-negative");
  }
  const auto& sc = race.safety_car;
  if (sc.start_probability_per_lap < 0 || sc.start_probability_per_lap > 1) {
    return bad("safety car probability must be in [0, 1]");
  }
  if (sc.min_duration_laps < 1 || sc.max_duration_laps < sc.min_duration_laps) {
    return bad("invalid safety car duration range");
  }
  if (race.noise.lap_sigma < 0 || race.noise.race_pace_sigma < 0) {
    return bad("noise sigmas must be non-negative");
  }

  std::set<int> grid;
  for (std::size_t i = 0; i < race.drivers.size(); ++i) {
    const auto& d = race.drivers[i];
    if (d.base_lap_time <= 0) return bad("driver " + d.code + ": base lap time must be positive");
    if (d.dnf_probability < 0 || d.dnf_probability > 1) {
      return bad("driver " + d.code + ": dnf probability must be in [0, 1]");
    }
    if (d.grid_position < 1 || !grid.insert(d.grid_position).second) {
      return bad("driver " + d.code + ": grid positions must be unique and >= 1");
    }
    if (auto v = validate_strategy(d.strategy, race); !v.ok) {
      return bad("driver " + d.code + ": " + v.error);
    }
  }
  return {};
}

std::string confidence_name(TireConfidence c) {
  switch (c) {
    case TireConfidence::Race: return "race";
    case TireConfidence::RaceLow: return "race_low";
    case TireConfidence::Pooled: return "pooled";
    case TireConfidence::Fallback: return "fallback";
  }
  return "unknown";
}

std::vector<std::string> strategy_notes(const Strategy& strategy, const RaceConfig& race) {
  static const char* kNames[] = {"soft", "medium", "hard"};
  std::vector<std::string> notes;
  for (const auto& stint : to_stints(strategy, race.total_laps)) {
    const auto& c = race.compounds[stint.compound_index];
    const std::string tire = std::string(kNames[static_cast<int>(c.designation)]) + " (C" +
                             std::to_string(c.absolute_compound) + ")";
    const std::string where = "laps " + std::to_string(stint.start_lap) + "-" +
                              std::to_string(stint.end_lap);
    switch (c.confidence) {
      case TireConfidence::Race: break;
      case TireConfidence::RaceLow:
        notes.push_back(where + " on " + tire + ": wear fitted from few clean laps");
        break;
      case TireConfidence::Pooled:
        notes.push_back(where + " on " + tire + ": wear estimated from other races");
        break;
      case TireConfidence::Fallback:
        notes.push_back(where + " on " + tire + ": wear borrowed from a neighboring compound");
        break;
    }
    if (c.max_stint_laps > 0 && stint.length() > c.max_stint_laps) {
      notes.push_back(where + " on " + tire + ": " + std::to_string(stint.length()) +
                      "-lap stint is longer than the " + std::to_string(c.max_stint_laps) +
                      " laps observed");
    }
  }
  return notes;
}

std::string format_strategy(const Strategy& strategy, const RaceConfig& race) {
  std::ostringstream out;
  auto letter = [&](int idx) {
    if (idx < 0 || idx >= static_cast<int>(race.compounds.size())) return '?';
    return designation_letter(race.compounds[idx].designation);
  };
  out << letter(strategy.start_compound_index);
  for (const auto& stop : strategy.stops) {
    out << '-' << stop.lap << '-' << letter(stop.compound_index);
  }
  return out.str();
}

}  // namespace f1sim

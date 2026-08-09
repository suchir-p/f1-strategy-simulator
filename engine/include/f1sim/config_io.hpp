#pragma once
// JSON (de)serialization for race configs, strategies and results. The same
// JSON shape is produced by the Python calibration pipeline, read by the CLI,
// and returned by the API.

#include <string>

#include <nlohmann/json.hpp>

#include "f1sim/monte_carlo.hpp"
#include "f1sim/optimizer.hpp"
#include "f1sim/types.hpp"

namespace f1sim {

RaceConfig race_from_json(const nlohmann::json& j);
RaceConfig load_race(const std::string& path);
nlohmann::json race_to_json(const RaceConfig& race);

// Strategy JSON uses weekend designations, e.g.
//   {"start": "M", "stops": [{"lap": 18, "compound": "H"}]}
Strategy strategy_from_json(const nlohmann::json& j, const RaceConfig& race);
nlohmann::json strategy_to_json(const Strategy& s, const RaceConfig& race);

// Compact text form: "M-18-H", "S-12-M-35-H".
Strategy parse_strategy(const std::string& text, const RaceConfig& race);

int compound_index_for(Designation d, const RaceConfig& race);  // Throws if absent.
int driver_index_for(const std::string& code, const RaceConfig& race);  // Throws if absent.

nlohmann::json stats_to_json(const OutcomeStats& s);
nlohmann::json optimizer_to_json(const OptimizerResult& r, const RaceConfig& race,
                                 std::size_t top_k);

}  // namespace f1sim

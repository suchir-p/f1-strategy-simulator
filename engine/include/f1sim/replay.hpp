#pragma once
// One simulated race, lap by lap, for the dashboard's race trace.
//
// A Monte Carlo result is a distribution; a replay picks one scenario out of
// it and records every lap. `typical_scenario` chooses a scenario whose
// result for the target driver is the median finishing position of a sample,
// so the race shown is representative rather than the luckiest or unluckiest.
// Because randomness is keyed by scenario id, a replay of scenario k is
// exactly the race that scenario k contributed to the Monte Carlo result.

#include <cstdint>
#include <vector>

#include "f1sim/monte_carlo.hpp"

namespace f1sim {

struct TypicalScenario {
  std::uint64_t scenario = 0;
  int sample_size = 0;
  int median_position_if_finished = 0;  // 0 if the driver never finished.
  int position = 0;                     // Scored position in the chosen scenario.
};

// Runs `config.scenarios` races and returns the first scenario whose scored
// position equals the median finishing position (or the closest finisher;
// if the driver never finishes, scenario `first_scenario`).
TypicalScenario typical_scenario(const RaceSimulator& sim, int target_driver,
                                 const Strategy& target_strategy, const MonteCarloConfig& config);

struct RaceReplay {
  std::uint64_t scenario = 0;
  std::vector<Strategy> strategies;  // By driver index (target's replaced).
  RaceResult result;
  std::vector<LapSnapshot> laps;     // laps[k] is the state after lap k + 1.
};

RaceReplay replay_race(const RaceSimulator& sim, int target_driver, const Strategy& target_strategy,
                       std::uint64_t seed, std::uint64_t scenario,
                       const SimulationOptions& options);

}  // namespace f1sim

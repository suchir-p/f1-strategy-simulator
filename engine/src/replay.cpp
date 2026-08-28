#include "f1sim/replay.hpp"

#include <cstdlib>

namespace f1sim {

TypicalScenario typical_scenario(const RaceSimulator& sim, int target_driver,
                                 const Strategy& target_strategy, const MonteCarloConfig& config) {
  const MonteCarloResult mc = run_monte_carlo(sim, target_driver, target_strategy, config);
  const int field = static_cast<int>(sim.race().drivers.size());
  TypicalScenario out;
  out.sample_size = config.scenarios;
  out.median_position_if_finished = mc.stats.p50_if_finished;
  out.scenario = config.first_scenario;
  out.position = mc.scored_positions.empty() ? 0 : mc.scored_positions.front();
  if (mc.stats.p50_if_finished == 0) return out;  // Never finished: nothing typical to show.

  int best_gap = field + 2;
  for (std::size_t s = 0; s < mc.scored_positions.size(); ++s) {
    const int p = mc.scored_positions[s];
    if (p > field) continue;  // Retired.
    const int gap = std::abs(p - mc.stats.p50_if_finished);
    if (gap < best_gap) {
      best_gap = gap;
      out.scenario = config.first_scenario + s;
      out.position = p;
      if (gap == 0) break;
    }
  }
  return out;
}

RaceReplay replay_race(const RaceSimulator& sim, int target_driver, const Strategy& target_strategy,
                       std::uint64_t seed, std::uint64_t scenario,
                       const SimulationOptions& options) {
  RaceReplay out;
  out.scenario = scenario;
  out.strategies = sim.default_strategies();
  out.strategies.at(static_cast<std::size_t>(target_driver)) = target_strategy;
  out.result = sim.run(out.strategies, KeyedRng(seed), scenario, options, &out.laps);
  return out;
}

}  // namespace f1sim

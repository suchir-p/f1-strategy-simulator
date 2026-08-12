#pragma once
// Monte Carlo evaluation of one driver's strategy against a fixed field.
//
// Scenario s always uses scenario id (first_scenario + s) with the same master
// seed, so two strategies evaluated with the same config see matched
// randomness. Each scenario writes only to its own slot in the output arrays
// and aggregation runs sequentially afterwards, so results are bit-identical
// for any OpenMP thread count.

#include <cstdint>
#include <vector>

#include "f1sim/simulator.hpp"

namespace f1sim {

struct MonteCarloConfig {
  std::uint64_t seed = 42;
  int scenarios = 1000;
  std::uint64_t first_scenario = 0;
  int threads = 0;  // 0 = OpenMP default (OMP_NUM_THREADS or all cores).
  SimulationOptions options;
};

struct OutcomeStats {
  int scenarios = 0;
  double mean_position = 0.0;  // DNFs scored as (field size + 1).
  double std_position = 0.0;
  int p05_position = 0;        // Empirical percentiles of the scored position.
  int p50_position = 0;
  int p95_position = 0;
  double p_win = 0.0;
  double p_podium = 0.0;
  double p_points = 0.0;       // Top 10.
  double dnf_rate = 0.0;
  double mean_race_time = 0.0; // Over finishing scenarios only (0 if none finished).
  // The same outcome split into "does the driver finish?" and "where, if so?".
  // Mixing DNFs into one distribution makes intervals look wider (and better
  // calibrated) than the finishing-position uncertainty really is.
  double finish_probability = 0.0;
  double mean_position_if_finished = 0.0;  // 0 if the driver never finished.
  int p05_if_finished = 0;
  int p50_if_finished = 0;
  int p95_if_finished = 0;
  // histogram[k] = probability of finishing in position k+1 (k < field size);
  // the final entry is the DNF probability.
  std::vector<double> histogram;
};

struct MonteCarloResult {
  OutcomeStats stats;
  std::vector<int> scored_positions;  // Per scenario; DNF = field size + 1.
  std::vector<double> race_times;     // Per scenario (time at last completed lap).
};

// Runs `config.scenarios` races where `target_driver` uses `target_strategy`
// and everyone else uses their default strategy.
MonteCarloResult run_monte_carlo(const RaceSimulator& sim, int target_driver,
                                 const Strategy& target_strategy, const MonteCarloConfig& config);

// Full-field version for validating against historical results: every driver
// uses their default strategy. Returns one OutcomeStats per driver.
std::vector<OutcomeStats> run_field_monte_carlo(const RaceSimulator& sim,
                                                const MonteCarloConfig& config);

// Mean of (a - b) over matched scenarios with a normal-approximation 95%
// interval. Both vectors must come from the same seed and scenario ids.
struct PairedDifference {
  double mean = 0.0;
  double ci_low = 0.0;
  double ci_high = 0.0;
};
PairedDifference paired_difference(const std::vector<int>& a, const std::vector<int>& b);

// Aggregates per-scenario scored positions (exposed for testing).
OutcomeStats summarize(const std::vector<int>& scored_positions,
                       const std::vector<double>& race_times, int field_size);

}  // namespace f1sim

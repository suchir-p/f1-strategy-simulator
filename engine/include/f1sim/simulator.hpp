#pragma once
// Lap-by-lap simulation of a full field.
//
// Model summary (see docs/model.md for the full description):
//   lap_time = T_driver + Delta_compound + alpha_c*a + beta_c*a^2
//            + gamma*fuel_laps + delta_driver,scenario + eps_driver,lap
// plus, separately: pit loss (green or safety car), dirty-air penalty,
// being held up behind a slower car, and safety-car laps run at a common
// pace with gaps compressed to `bunched_gap`.

#include <cstdint>
#include <vector>

#include "f1sim/rng.hpp"
#include "f1sim/types.hpp"

namespace f1sim {

struct SimulationOptions {
  // false = deterministic engine: no lap noise, no race-pace offsets, no random
  // safety cars or retirements, and overtakes succeed iff their probability
  // is >= 0.5. Useful for unit tests and for debugging the race logic.
  bool stochastic = true;
  // Laps forced to run under the safety car (in addition to random ones).
  std::vector<int> forced_safety_car_laps;
  // Laps forced to run under the virtual safety car (ignored on SC laps).
  std::vector<int> forced_vsc_laps;
  // Forced retirements: (driver index, lap). Applied in both modes.
  std::vector<std::pair<int, int>> forced_retirements;
};

// Everything random about a scenario that does not depend on strategy.
// Built purely from keyed draws, so it is identical for every candidate
// strategy evaluated on the same scenario id.
struct ScenarioEvents {
  std::vector<std::uint8_t> safety_car;  // Index by lap (size total_laps + 1).
  std::vector<std::uint8_t> vsc;         // Virtual safety car, same indexing; never on an SC lap.
  std::vector<int> retirement_lap;       // 0 = does not retire.
  std::vector<double> race_pace_offset;  // delta_driver,scenario.
};

ScenarioEvents build_scenario_events(const RaceConfig& race, const KeyedRng& rng,
                                     std::uint64_t scenario, const SimulationOptions& options);

// Optional per-lap trace for tests, debugging and the frontend replay.
struct LapSnapshot {
  int lap = 0;
  bool safety_car = false;
  bool virtual_safety_car = false;
  std::vector<double> cumulative_time;  // By driver index.
  std::vector<int> position;            // By driver index.
  std::vector<int> compound_index;      // By driver index (after any stop this lap).
  std::vector<int> tire_age;            // By driver index (after this lap).
  std::vector<DriverStatus> status;     // By driver index.
  std::vector<std::uint8_t> pitted;     // By driver index.
};

class RaceSimulator {
 public:
  explicit RaceSimulator(const RaceConfig& race);

  // Simulates one race. `strategies[i]` is driver i's strategy; it must be
  // valid for this race (validate first; this function does not re-check).
  RaceResult run(const std::vector<Strategy>& strategies, const KeyedRng& rng,
                 std::uint64_t scenario, const SimulationOptions& options,
                 std::vector<LapSnapshot>* trace = nullptr) const;

  // Same, using precomputed scenario events (lets the optimizer reuse them).
  RaceResult run(const std::vector<Strategy>& strategies, const KeyedRng& rng,
                 std::uint64_t scenario, const SimulationOptions& options,
                 const ScenarioEvents& events,
                 std::vector<LapSnapshot>* trace = nullptr) const;

  // Clean-air lap time for a driver, excluding noise and interactions.
  double clean_lap_time(int driver, int compound_index, int tire_age, int lap) const;

  const RaceConfig& race() const { return race_; }
  double vsc_lap_time() const { return vsc_lap_time_; }
  double vsc_pit_loss() const { return vsc_pit_loss_; }
  std::vector<Strategy> default_strategies() const;

 private:
  RaceConfig race_;
  double vsc_lap_time_;  // Resolved defaults (see VirtualSafetyCarParams).
  double vsc_pit_loss_;
};

}  // namespace f1sim

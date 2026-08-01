#pragma once
// Core data types shared by the simulator, Monte Carlo runner and optimizer.
//
// Units: all times are in seconds. Laps are 1-indexed (lap 1 is the first racing
// lap). Tire age counts completed laps on the current set (0 on a fresh set).

#include <cstdint>
#include <string>
#include <vector>

namespace f1sim {

// Weekend-relative designation. The absolute Pirelli compound (C1..C6) is
// stored separately, because e.g. C3 can be the "soft" at one race and the
// "medium" at another.
enum class Designation : std::uint8_t { Soft = 0, Medium = 1, Hard = 2 };

// Where a compound's pace and wear parameters came from, best first.
enum class TireConfidence : std::uint8_t {
  Race = 0,     // Fitted on this race with plenty of clean laps.
  RaceLow = 1,  // Fitted on this race, but from few clean laps.
  Pooled = 2,   // Estimated from the same Pirelli compound at other races.
  Fallback = 3, // Borrowed from a neighboring compound, made conservative.
};

struct CompoundParams {
  Designation designation = Designation::Medium;
  int absolute_compound = 3;  // Pirelli C-number, e.g. 3 for C3.
  double pace_offset = 0.0;   // Delta_compound: fresh-tire pace vs. driver baseline (s).
  double deg_linear = 0.0;    // alpha_c: seconds per lap of tire age.
  double deg_quadratic = 0.0; // beta_c: seconds per (lap of tire age)^2.
  int max_stint_laps = 0;     // Longest tire age the wear curve was fitted on (0 = unknown).
  TireConfidence confidence = TireConfidence::Race;
};

struct PitStop {
  int lap = 0;                // Driver pits at the END of this lap.
  int compound_index = 0;     // Index into RaceConfig::compounds for the new set.

  bool operator==(const PitStop&) const = default;
};

struct Strategy {
  int start_compound_index = 0;
  std::vector<PitStop> stops;  // Sorted by lap.

  bool operator==(const Strategy&) const = default;
};

struct DriverConfig {
  std::string code;            // e.g. "VER".
  double base_lap_time = 90.0; // T_driver,circuit: clean-air, zero-fuel-effect baseline.
  int grid_position = 1;       // 1-indexed starting position.
  double dnf_probability = 0.0;// Probability of retiring at some point in the race.
  Strategy strategy;           // Default (e.g. historical) strategy.
};

struct SafetyCarParams {
  double start_probability_per_lap = 0.0; // Chance a safety car is deployed on a green lap.
  int min_duration_laps = 2;
  int max_duration_laps = 5;
  double lap_time = 120.0;     // Lap time behind the safety car (whole field).
  double bunched_gap = 1.0;    // Max gap to the car ahead while bunched (s).
  int earliest_lap = 2;        // No deployments before this lap.
  int latest_lap_margin = 2;   // No deployments in the final N laps.
};

// Virtual safety car: every car slows to a fixed lap time, so gaps are kept
// (no bunching, unlike the safety car), nobody races, and a pit stop costs
// less than under green because the cars on track are slow too.
struct VirtualSafetyCarParams {
  double start_probability_per_lap = 0.0;
  int min_duration_laps = 1;
  int max_duration_laps = 3;
  double lap_time = 0.0;       // Lap time under the VSC (s); 0 = same as the safety car's.
  int earliest_lap = 2;
  int latest_lap_margin = 1;
};

struct InteractionParams {
  double min_gap = 0.3;            // Closest a following car can be at the line (s).
  double dirty_air_gap = 1.0;      // Within this gap the follower loses time.
  double dirty_air_penalty = 0.25; // Seconds lost per lap in dirty air.
  double overtake_pace_threshold = 0.4; // Pace advantage needed to attempt a pass (s/lap).
  double overtake_base_probability = 0.25; // Pass chance at exactly the threshold.
  double overtake_probability_per_second = 0.35; // Extra chance per extra second of pace.
  double max_overtake_probability = 0.9;
};

struct NoiseParams {
  double race_pace_sigma = 0.15; // sigma_race: persistent per-driver, per-scenario offset.
  double lap_sigma = 0.35;       // sigma_lap: independent per-driver, per-lap noise.
};

struct RaceConfig {
  std::string name;
  int season = 2024;
  int total_laps = 50;
  double fuel_effect_per_lap = 0.03; // gamma: seconds per lap of fuel remaining
                                     // (in practice also absorbs track evolution).
  double pit_loss_green = 22.0;      // Effective pit loss under green flag (s).
  double pit_loss_safety_car = 11.0; // Effective pit loss under safety car (s).
  double pit_loss_virtual_safety_car = 0.0; // Under VSC (s); 0 = midway between green and SC.
  double grid_spacing = 0.25;        // Time gap per grid slot at the start (s).
  int min_compounds_required = 2;    // Dry-race rule: at least two distinct compounds.
  std::vector<CompoundParams> compounds;
  std::vector<DriverConfig> drivers;
  SafetyCarParams safety_car;
  VirtualSafetyCarParams virtual_safety_car;
  InteractionParams interactions;
  NoiseParams noise;
};

enum class DriverStatus : std::uint8_t { Running = 0, Finished = 1, Retired = 2 };

struct DriverResult {
  int driver_index = 0;
  int position = 0;          // 1-indexed classified position (retired drivers last).
  DriverStatus status = DriverStatus::Finished;
  int laps_completed = 0;
  double race_time = 0.0;    // Cumulative time at the last completed lap.
  int pit_stops = 0;
};

struct RaceResult {
  std::vector<DriverResult> classification; // Sorted by position.
  std::vector<int> position_by_driver;      // position_by_driver[i] = position of driver i.
  std::vector<int> safety_car_laps;         // Laps run under the safety car.
  std::vector<int> virtual_safety_car_laps; // Laps run under the virtual safety car.
};

}  // namespace f1sim

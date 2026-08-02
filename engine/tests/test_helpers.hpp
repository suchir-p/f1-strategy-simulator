#pragma once
// Small, hand-checkable race configs for unit tests.

#include <string>

#include "f1sim/types.hpp"

namespace f1sim::testing {

// Compound indices in make_race(): 0 = soft (C4), 1 = medium (C3), 2 = hard (C2).
inline constexpr int kSoft = 0;
inline constexpr int kMedium = 1;
inline constexpr int kHard = 2;

inline Strategy one_stop(int start, int lap, int next) { return Strategy{start, {{lap, next}}}; }

inline RaceConfig make_race(int drivers = 4, int laps = 20) {
  RaceConfig r;
  r.name = "unit test race";
  r.total_laps = laps;
  r.fuel_effect_per_lap = 0.03;
  r.pit_loss_green = 20.0;
  r.pit_loss_safety_car = 10.0;
  r.grid_spacing = 0.25;
  r.compounds = {
      {Designation::Soft, 4, 0.0, 0.10, 0.002},
      {Designation::Medium, 3, 0.4, 0.06, 0.001},
      {Designation::Hard, 2, 0.8, 0.03, 0.0005},
  };
  r.safety_car.start_probability_per_lap = 0.0;
  r.safety_car.lap_time = 110.0;
  r.safety_car.bunched_gap = 1.0;
  r.noise = {0.15, 0.3};
  for (int i = 0; i < drivers; ++i) {
    DriverConfig d;
    d.code = "D" + std::to_string(i);
    d.base_lap_time = 80.0 + 0.3 * i;
    d.grid_position = i + 1;
    d.strategy = one_stop(kMedium, laps / 2, kHard);
    r.drivers.push_back(d);
  }
  return r;
}

}  // namespace f1sim::testing

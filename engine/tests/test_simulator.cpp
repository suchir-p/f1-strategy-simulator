#include <gtest/gtest.h>

#include <algorithm>
#include <numeric>

#include "f1sim/simulator.hpp"
#include "f1sim/strategy.hpp"
#include "test_helpers.hpp"

using namespace f1sim;
using namespace f1sim::testing;

namespace {

SimulationOptions deterministic() {
  SimulationOptions o;
  o.stochastic = false;
  return o;
}

// Analytic race time for a lone driver: sum of clean laps plus pit losses.
double expected_solo_time(const RaceSimulator& sim, const Strategy& s,
                          const std::vector<int>& sc_laps = {}) {
  const auto& race = sim.race();
  double t = 0.0;
  for (const auto& stint : to_stints(s, race.total_laps)) {
    for (int lap = stint.start_lap; lap <= stint.end_lap; ++lap) {
      const bool sc = std::find(sc_laps.begin(), sc_laps.end(), lap) != sc_laps.end();
      t += sc ? race.safety_car.lap_time
              : sim.clean_lap_time(0, stint.compound_index, lap - stint.start_lap, lap);
    }
  }
  for (const auto& stop : s.stops) {
    const bool sc = std::find(sc_laps.begin(), sc_laps.end(), stop.lap) != sc_laps.end();
    t += sc ? race.pit_loss_safety_car : race.pit_loss_green;
  }
  return t;
}

}  // namespace

TEST(Simulator, LapTimeModelTerms) {
  const RaceSimulator sim(make_race(1, 20));
  const auto& c = sim.race().compounds[kSoft];
  // Lap 1, fresh soft: base + offset + fuel for 20 laps.
  EXPECT_DOUBLE_EQ(sim.clean_lap_time(0, kSoft, 0, 1), 80.0 + c.pace_offset + 0.03 * 20);
  // Tire age 10 on the last lap.
  EXPECT_DOUBLE_EQ(sim.clean_lap_time(0, kSoft, 10, 20),
                   80.0 + c.pace_offset + c.deg_linear * 10 + c.deg_quadratic * 100 + 0.03);
  // Softer is faster when fresh, slower once worn.
  EXPECT_LT(sim.clean_lap_time(0, kSoft, 0, 5), sim.clean_lap_time(0, kHard, 0, 5));
  EXPECT_GT(sim.clean_lap_time(0, kSoft, 25, 5), sim.clean_lap_time(0, kHard, 25, 5));
}

TEST(Simulator, SoloRaceMatchesAnalyticTime) {
  const RaceSimulator sim(make_race(1, 20));
  for (const Strategy& s : {one_stop(kMedium, 10, kHard), Strategy{kSoft, {{6, kMedium}, {13, kHard}}}}) {
    const auto r = sim.run({s}, KeyedRng(1), 0, deterministic());
    ASSERT_EQ(r.classification.size(), 1u);
    EXPECT_EQ(r.classification[0].status, DriverStatus::Finished);
    EXPECT_EQ(r.classification[0].laps_completed, 20);
    EXPECT_EQ(r.classification[0].pit_stops, static_cast<int>(s.stops.size()));
    EXPECT_NEAR(r.classification[0].race_time, expected_solo_time(sim, s), 1e-9);
  }
}

TEST(Simulator, SafetyCarPitStopIsCheaper) {
  const RaceSimulator sim(make_race(1, 20));
  const Strategy s = one_stop(kMedium, 10, kHard);
  SimulationOptions opts = deterministic();
  opts.forced_safety_car_laps = {9, 10, 11};
  const auto r = sim.run({s}, KeyedRng(1), 0, opts);
  EXPECT_NEAR(r.classification[0].race_time, expected_solo_time(sim, s, {9, 10, 11}), 1e-9);
  EXPECT_EQ(r.safety_car_laps, (std::vector<int>{9, 10, 11}));
}

TEST(Simulator, DeterministicRunsAreIdentical) {
  const RaceSimulator sim(make_race(6, 30));
  const auto s = sim.default_strategies();
  const auto a = sim.run(s, KeyedRng(5), 3, deterministic());
  const auto b = sim.run(s, KeyedRng(99), 7, deterministic());  // Seed is irrelevant here.
  EXPECT_EQ(a.position_by_driver, b.position_by_driver);
  for (std::size_t i = 0; i < a.classification.size(); ++i) {
    EXPECT_DOUBLE_EQ(a.classification[i].race_time, b.classification[i].race_time);
  }
}

TEST(Simulator, StochasticRunsReproduceWithSameKey) {
  auto race = make_race(10, 40);
  race.safety_car.start_probability_per_lap = 0.05;
  for (auto& d : race.drivers) d.dnf_probability = 0.1;
  const RaceSimulator sim(race);
  const auto s = sim.default_strategies();
  const SimulationOptions opts;
  const auto a = sim.run(s, KeyedRng(5), 11, opts);
  const auto b = sim.run(s, KeyedRng(5), 11, opts);
  EXPECT_EQ(a.position_by_driver, b.position_by_driver);
  EXPECT_EQ(a.safety_car_laps, b.safety_car_laps);
  for (std::size_t i = 0; i < a.classification.size(); ++i) {
    EXPECT_EQ(a.classification[i].race_time, b.classification[i].race_time);
  }
}

TEST(Simulator, RaceStateInvariantsHoldEveryLap) {
  auto race = make_race(20, 50);
  race.safety_car.start_probability_per_lap = 0.04;
  for (auto& d : race.drivers) d.dnf_probability = 0.1;
  // Mix of strategies.
  for (std::size_t i = 0; i < race.drivers.size(); ++i) {
    race.drivers[i].strategy = i % 2 == 0 ? one_stop(kSoft, 15, kHard)
                                          : Strategy{kMedium, {{12, kHard}, {33, kSoft}}};
  }
  const RaceSimulator sim(race);
  const auto strategies = sim.default_strategies();
  const int n = static_cast<int>(race.drivers.size());

  for (std::uint64_t scenario = 0; scenario < 50; ++scenario) {
    std::vector<LapSnapshot> trace;
    const auto r = sim.run(strategies, KeyedRng(17), scenario, SimulationOptions{}, &trace);
    ASSERT_EQ(static_cast<int>(trace.size()), race.total_laps);

    std::vector<double> prev_time(n, -1.0);
    std::vector<int> stops_seen(n, 0);
    for (const auto& snap : trace) {
      // Positions are a permutation of 1..n.
      std::vector<int> pos = snap.position;
      std::sort(pos.begin(), pos.end());
      for (int p = 0; p < n; ++p) ASSERT_EQ(pos[p], p + 1);

      for (int d = 0; d < n; ++d) {
        ASSERT_GE(snap.tire_age[d], 0);
        if (snap.status[d] == DriverStatus::Running) {
          ASSERT_GT(snap.cumulative_time[d], prev_time[d]);  // Time always advances.
          prev_time[d] = snap.cumulative_time[d];
        }
        if (snap.pitted[d]) {
          ASSERT_EQ(snap.tire_age[d], 0);
          const auto& stop = strategies[d].stops[stops_seen[d]++];
          ASSERT_EQ(stop.lap, snap.lap);
          ASSERT_EQ(snap.compound_index[d], stop.compound_index);
        }
      }
      // Running cars are ordered by time and ahead of retired cars.
      std::vector<int> order(n);
      for (int d = 0; d < n; ++d) order[snap.position[d] - 1] = d;
      bool seen_retired = false;
      for (int p = 0; p < n; ++p) {
        const int d = order[p];
        if (snap.status[d] == DriverStatus::Retired) {
          seen_retired = true;
          continue;
        }
        ASSERT_FALSE(seen_retired) << "running car classified behind a retired car";
        if (p > 0) ASSERT_GE(snap.cumulative_time[d], snap.cumulative_time[order[p - 1]]);
      }
    }
    // Finishers made every planned stop; retirements stop early.
    for (const auto& dr : r.classification) {
      if (dr.status == DriverStatus::Finished) {
        ASSERT_EQ(dr.laps_completed, race.total_laps);
        ASSERT_EQ(dr.pit_stops, static_cast<int>(strategies[dr.driver_index].stops.size()));
      } else {
        ASSERT_LT(dr.laps_completed, race.total_laps);
      }
    }
  }
}

TEST(Simulator, RetiredDriverIsClassifiedLast) {
  const RaceSimulator sim(make_race(4, 20));
  SimulationOptions opts = deterministic();
  opts.forced_retirements = {{0, 8}};  // The fastest driver retires on lap 8.
  const auto r = sim.run(sim.default_strategies(), KeyedRng(1), 0, opts);
  EXPECT_EQ(r.position_by_driver[0], 4);
  const auto& dr = r.classification.back();
  EXPECT_EQ(dr.driver_index, 0);
  EXPECT_EQ(dr.status, DriverStatus::Retired);
  EXPECT_EQ(dr.laps_completed, 7);
}

TEST(Simulator, MuchFasterCarOvertakes) {
  auto race = make_race(2, 20);
  race.drivers[0].base_lap_time = 81.5;  // Slow car on pole.
  race.drivers[1].base_lap_time = 80.0;  // 1.5 s/lap faster.
  const RaceSimulator sim(race);
  const auto r = sim.run(sim.default_strategies(), KeyedRng(1), 0, deterministic());
  EXPECT_EQ(r.position_by_driver[1], 1);
}

TEST(Simulator, SlightlyFasterCarIsHeldUp) {
  auto race = make_race(2, 20);
  race.drivers[0].base_lap_time = 80.2;
  race.drivers[1].base_lap_time = 80.0;  // Only 0.2 s/lap faster: below the threshold.
  const RaceSimulator sim(race);
  std::vector<LapSnapshot> trace;
  const auto r = sim.run(sim.default_strategies(), KeyedRng(1), 0, deterministic(), &trace);
  EXPECT_EQ(r.position_by_driver[0], 1);
  const double gap = r.classification[1].race_time - r.classification[0].race_time;
  EXPECT_NEAR(gap, race.interactions.min_gap, 1e-9);
}

TEST(Simulator, SafetyCarBunchesTheField) {
  auto race = make_race(5, 20);
  for (std::size_t i = 0; i < race.drivers.size(); ++i) race.drivers[i].base_lap_time = 80.0 + 1.0 * i;
  const RaceSimulator sim(race);
  SimulationOptions opts = deterministic();
  opts.forced_safety_car_laps = {6, 7};
  std::vector<LapSnapshot> trace;
  sim.run(sim.default_strategies(), KeyedRng(1), 0, opts, &trace);
  const auto& after = trace[6];  // Lap 7, the last SC lap.
  ASSERT_TRUE(after.safety_car);
  std::vector<int> order(5);
  for (int d = 0; d < 5; ++d) order[after.position[d] - 1] = d;
  for (int p = 1; p < 5; ++p) {
    const double gap = after.cumulative_time[order[p]] - after.cumulative_time[order[p - 1]];
    EXPECT_LE(gap, race.safety_car.bunched_gap + 1e-9);
    EXPECT_GE(gap, 0.0);
  }
  // Before the safety car the gaps were far larger.
  const auto& before = trace[4];
  EXPECT_GT(before.cumulative_time[4] - before.cumulative_time[0], 10.0);
}

TEST(Simulator, GridOrderAtTheStart) {
  auto race = make_race(4, 20);
  // Reverse the grid: slowest driver on pole.
  for (int i = 0; i < 4; ++i) race.drivers[i].grid_position = 4 - i;
  // Equal pace, so nobody can pass.
  for (auto& d : race.drivers) d.base_lap_time = 80.0;
  const RaceSimulator sim(race);
  const auto r = sim.run(sim.default_strategies(), KeyedRng(1), 0, deterministic());
  for (int i = 0; i < 4; ++i) EXPECT_EQ(r.position_by_driver[i], 4 - i);
}

TEST(ScenarioEvents, IndependentOfStrategyAndDeterministic) {
  auto race = make_race(20, 50);
  race.safety_car.start_probability_per_lap = 0.05;
  for (auto& d : race.drivers) d.dnf_probability = 0.2;
  const KeyedRng rng(123);
  for (std::uint64_t s = 0; s < 20; ++s) {
    const auto a = build_scenario_events(race, rng, s, SimulationOptions{});
    race.drivers[0].strategy = one_stop(kSoft, 15, kHard);  // Strategy change.
    const auto b = build_scenario_events(race, rng, s, SimulationOptions{});
    EXPECT_EQ(a.safety_car, b.safety_car);
    EXPECT_EQ(a.retirement_lap, b.retirement_lap);
    EXPECT_EQ(a.race_pace_offset, b.race_pace_offset);
  }
}

TEST(ScenarioEvents, SafetyCarRespectsWindow) {
  auto race = make_race(4, 30);
  race.safety_car.start_probability_per_lap = 1.0;  // Deploy whenever allowed.
  race.safety_car.earliest_lap = 5;
  race.safety_car.latest_lap_margin = 3;
  race.safety_car.min_duration_laps = race.safety_car.max_duration_laps = 2;
  const auto ev = build_scenario_events(race, KeyedRng(1), 0, SimulationOptions{});
  for (int lap = 1; lap < 5; ++lap) EXPECT_EQ(ev.safety_car[lap], 0) << lap;
  EXPECT_EQ(ev.safety_car[5], 1);
  EXPECT_EQ(ev.safety_car[6], 1);
  EXPECT_EQ(ev.safety_car[7], 0);  // Mandatory green lap between deployments.
  EXPECT_EQ(ev.safety_car[8], 1);
}

// ---- virtual safety car ---------------------------------------------------------

TEST(Simulator, VirtualSafetyCarKeepsGapsAndOrder) {
  auto race = make_race(5, 20);
  for (std::size_t i = 0; i < race.drivers.size(); ++i) race.drivers[i].base_lap_time = 80.0 + 1.0 * i;
  race.virtual_safety_car.lap_time = 110.0;
  const RaceSimulator sim(race);
  SimulationOptions opts = deterministic();
  opts.forced_vsc_laps = {6, 7};
  std::vector<LapSnapshot> trace;
  const auto r = sim.run(sim.default_strategies(), KeyedRng(1), 0, opts, &trace);
  ASSERT_TRUE(trace[5].virtual_safety_car && trace[6].virtual_safety_car);
  EXPECT_FALSE(trace[5].safety_car);
  EXPECT_EQ(r.virtual_safety_car_laps, (std::vector<int>{6, 7}));
  EXPECT_TRUE(r.safety_car_laps.empty());
  // Unlike the safety car, gaps are exactly what they were before the VSC.
  for (int d = 1; d < 5; ++d) {
    const double before = trace[4].cumulative_time[d] - trace[4].cumulative_time[0];
    const double during = trace[6].cumulative_time[d] - trace[6].cumulative_time[0];
    EXPECT_NEAR(during, before, 1e-9);
    EXPECT_EQ(trace[6].position[d], trace[4].position[d]);
  }
  EXPECT_NEAR(trace[6].cumulative_time[0] - trace[4].cumulative_time[0], 220.0, 1e-9);
}

TEST(Simulator, VirtualSafetyCarStopCostsBetweenGreenAndSafetyCar) {
  // Resolved default: midway between the green and safety-car pit losses.
  auto race = make_race(1, 20);
  const RaceSimulator sim(race);
  EXPECT_DOUBLE_EQ(sim.vsc_pit_loss(), 0.5 * (race.pit_loss_green + race.pit_loss_safety_car));
  EXPECT_DOUBLE_EQ(sim.vsc_lap_time(), race.safety_car.lap_time);
  race.pit_loss_virtual_safety_car = 13.0;
  race.virtual_safety_car.lap_time = 105.0;
  const RaceSimulator custom(race);
  const Strategy s = one_stop(kMedium, 10, kHard);
  SimulationOptions opts = deterministic();
  const double green = custom.run({s}, KeyedRng(1), 0, opts).classification[0].race_time;
  opts.forced_vsc_laps = {10};
  const double with_vsc = custom.run({s}, KeyedRng(1), 0, opts).classification[0].race_time;
  // Lap 10 runs at the VSC time instead of racing pace, and the stop costs 13 s, not 20.
  const double lap10 = custom.clean_lap_time(0, kMedium, 9, 10);
  EXPECT_NEAR(with_vsc - green, (105.0 - lap10) + (13.0 - race.pit_loss_green), 1e-9);
}

TEST(ScenarioEvents, VirtualSafetyCarNeverOverlapsSafetyCar) {
  auto race = make_race(4, 50);
  race.safety_car.start_probability_per_lap = 0.05;
  race.virtual_safety_car.start_probability_per_lap = 0.08;
  const KeyedRng rng(3);
  int vsc_laps = 0;
  for (std::uint64_t s = 0; s < 300; ++s) {
    const auto ev = build_scenario_events(race, rng, s, SimulationOptions{});
    for (int lap = 1; lap <= 50; ++lap) {
      if (ev.vsc[lap]) {
        ++vsc_laps;
        EXPECT_FALSE(ev.safety_car[lap]);
        EXPECT_FALSE(ev.safety_car[lap - 1]);
        EXPECT_GE(lap, race.virtual_safety_car.earliest_lap);
      }
    }
  }
  EXPECT_GT(vsc_laps, 100);
}

TEST(ScenarioEvents, NoVirtualSafetyCarMeansUnchangedRandomness) {
  // A race with VSC probability 0 must draw exactly what it did before VSCs
  // existed: same safety cars, retirements and pace.
  auto race = make_race(6, 40);
  race.safety_car.start_probability_per_lap = 0.04;
  for (auto& d : race.drivers) d.dnf_probability = 0.1;
  auto with_vsc = race;
  with_vsc.virtual_safety_car.start_probability_per_lap = 0.05;
  const KeyedRng rng(11);
  for (std::uint64_t s = 0; s < 50; ++s) {
    const auto a = build_scenario_events(race, rng, s, SimulationOptions{});
    const auto b = build_scenario_events(with_vsc, rng, s, SimulationOptions{});
    EXPECT_EQ(a.safety_car, b.safety_car);  // VSC draws use their own keys.
    EXPECT_EQ(a.retirement_lap, b.retirement_lap);
    EXPECT_EQ(a.race_pace_offset, b.race_pace_offset);
    for (auto v : a.vsc) EXPECT_EQ(v, 0);
  }
}

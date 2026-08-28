#include <gtest/gtest.h>

#include "f1sim/replay.hpp"
#include "test_helpers.hpp"

using namespace f1sim;
using namespace f1sim::testing;

namespace {

RaceConfig busy_race() {
  auto race = make_race(20, 40);
  race.safety_car.start_probability_per_lap = 0.03;
  for (auto& d : race.drivers) d.dnf_probability = 0.05;
  return race;
}

MonteCarloConfig mc(int scenarios, int threads = 1) {
  MonteCarloConfig c;
  c.seed = 7;
  c.scenarios = scenarios;
  c.threads = threads;
  return c;
}

}  // namespace

TEST(PairedDifference, KnownValues) {
  const auto d = paired_difference({3, 4, 5, 6}, {2, 2, 2, 2});
  EXPECT_DOUBLE_EQ(d.mean, 2.5);
  EXPECT_LT(d.ci_low, 2.5);
  EXPECT_GT(d.ci_high, 2.5);
  EXPECT_NEAR(d.ci_high - d.mean, d.mean - d.ci_low, 1e-12);
  const auto same = paired_difference({1, 2, 3}, {1, 2, 3});
  EXPECT_DOUBLE_EQ(same.mean, 0.0);
  EXPECT_DOUBLE_EQ(same.ci_low, 0.0);
  EXPECT_THROW(paired_difference({1}, {1, 2}), std::invalid_argument);
}

TEST(Replay, MatchesTheMonteCarloScenario) {
  // A replay of scenario k is exactly the race scenario k contributed.
  const RaceSimulator sim(busy_race());
  const Strategy s = one_stop(kSoft, 15, kHard);
  const auto res = run_monte_carlo(sim, 3, s, mc(60, 4));
  for (std::uint64_t k : {0u, 17u, 59u}) {
    const auto r = replay_race(sim, 3, s, 7, k, MonteCarloConfig{}.options);
    const auto& dr = r.result.classification[r.result.position_by_driver[3] - 1];
    const int scored = dr.status == DriverStatus::Retired ? 21 : dr.position;
    EXPECT_EQ(scored, res.scored_positions[k]) << "scenario " << k;
    ASSERT_EQ(r.laps.size(), 40u);
    EXPECT_EQ(r.laps.back().position[3], r.result.position_by_driver[3]);
  }
}

TEST(Replay, TypicalScenarioFinishesAtTheMedian) {
  const RaceSimulator sim(busy_race());
  const Strategy s = one_stop(kMedium, 20, kHard);
  const auto t = typical_scenario(sim, 5, s, mc(200));
  EXPECT_EQ(t.sample_size, 200);
  ASSERT_GT(t.median_position_if_finished, 0);
  EXPECT_EQ(t.position, t.median_position_if_finished);
  const auto r = replay_race(sim, 5, s, 7, t.scenario, MonteCarloConfig{}.options);
  EXPECT_EQ(r.result.position_by_driver[5], t.position);
  // Same answer regardless of threads.
  EXPECT_EQ(typical_scenario(sim, 5, s, mc(200, 4)).scenario, t.scenario);
}

TEST(Replay, ForcedSafetyCarAppearsInTheTrace) {
  const RaceSimulator sim(make_race(6, 30));
  SimulationOptions o;
  o.forced_safety_car_laps = {10, 11, 12};
  const auto r = replay_race(sim, 0, one_stop(kSoft, 11, kHard), 1, 0, o);
  EXPECT_TRUE(r.laps[9].safety_car);
  EXPECT_TRUE(r.laps[11].safety_car);
  EXPECT_FALSE(r.laps[8].safety_car);
  EXPECT_TRUE(r.laps[10].pitted[0]);
}

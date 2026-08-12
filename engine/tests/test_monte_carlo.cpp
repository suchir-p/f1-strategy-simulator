#include <gtest/gtest.h>

#include <numeric>

#include "f1sim/monte_carlo.hpp"
#include "test_helpers.hpp"

using namespace f1sim;
using namespace f1sim::testing;

namespace {

RaceConfig stochastic_race() {
  auto race = make_race(20, 40);
  race.safety_car.start_probability_per_lap = 0.03;
  for (auto& d : race.drivers) d.dnf_probability = 0.05;
  return race;
}

}  // namespace

TEST(Summarize, KnownValues) {
  // Field of 3: DNF scores as 4.
  const auto s = summarize({1, 2, 2, 4}, {100.0, 101.0, 103.0, 50.0}, 3);
  EXPECT_EQ(s.scenarios, 4);
  EXPECT_DOUBLE_EQ(s.mean_position, 2.25);
  EXPECT_DOUBLE_EQ(s.p_win, 0.25);
  EXPECT_DOUBLE_EQ(s.p_podium, 0.75);
  EXPECT_DOUBLE_EQ(s.dnf_rate, 0.25);
  EXPECT_DOUBLE_EQ(s.mean_race_time, 304.0 / 3.0);  // Finishers only.
  ASSERT_EQ(s.histogram.size(), 4u);
  EXPECT_DOUBLE_EQ(s.histogram[0], 0.25);
  EXPECT_DOUBLE_EQ(s.histogram[1], 0.5);
  EXPECT_DOUBLE_EQ(s.histogram[3], 0.25);
  EXPECT_EQ(s.p50_position, 2);
  // Split view: finishes 3 of 4, and averages P1.67 when it does.
  EXPECT_DOUBLE_EQ(s.finish_probability, 0.75);
  EXPECT_DOUBLE_EQ(s.mean_position_if_finished, 5.0 / 3.0);
  EXPECT_EQ(s.p95_if_finished, 2);  // The DNF no longer sets the top of the range.
  EXPECT_EQ(s.p95_position, 4);
}

TEST(Summarize, NeverFinishing) {
  const auto s = summarize({4, 4}, {}, 3);
  EXPECT_DOUBLE_EQ(s.finish_probability, 0.0);
  EXPECT_DOUBLE_EQ(s.mean_position_if_finished, 0.0);
}

TEST(MonteCarlo, ReproducibleWithSameSeed) {
  const RaceSimulator sim(stochastic_race());
  MonteCarloConfig cfg;
  cfg.scenarios = 300;
  cfg.seed = 9;
  const auto a = run_monte_carlo(sim, 3, sim.race().drivers[3].strategy, cfg);
  const auto b = run_monte_carlo(sim, 3, sim.race().drivers[3].strategy, cfg);
  EXPECT_EQ(a.scored_positions, b.scored_positions);
  EXPECT_EQ(a.race_times, b.race_times);
  cfg.seed = 10;
  const auto c = run_monte_carlo(sim, 3, sim.race().drivers[3].strategy, cfg);
  EXPECT_NE(a.scored_positions, c.scored_positions);
}

TEST(MonteCarlo, IdenticalAcrossThreadCounts) {
  const RaceSimulator sim(stochastic_race());
  MonteCarloConfig cfg;
  cfg.scenarios = 500;
  cfg.threads = 1;
  const auto serial = run_monte_carlo(sim, 5, sim.race().drivers[5].strategy, cfg);
  for (int t : {2, 3, 4, 8}) {
    cfg.threads = t;
    const auto par = run_monte_carlo(sim, 5, sim.race().drivers[5].strategy, cfg);
    EXPECT_EQ(serial.scored_positions, par.scored_positions) << t << " threads";
    EXPECT_EQ(serial.race_times, par.race_times) << t << " threads";  // Bitwise equal.
    EXPECT_EQ(serial.stats.mean_position, par.stats.mean_position);
  }
}

TEST(MonteCarlo, ScenarioSlicesMatchOneBigRun) {
  // Scenario ids, not batch boundaries, define the randomness.
  const RaceSimulator sim(stochastic_race());
  MonteCarloConfig all;
  all.scenarios = 200;
  const auto whole = run_monte_carlo(sim, 0, sim.race().drivers[0].strategy, all);
  MonteCarloConfig second = all;
  second.scenarios = 100;
  second.first_scenario = 100;
  const auto tail = run_monte_carlo(sim, 0, sim.race().drivers[0].strategy, second);
  EXPECT_TRUE(std::equal(tail.scored_positions.begin(), tail.scored_positions.end(),
                         whole.scored_positions.begin() + 100));
}

TEST(MonteCarlo, DistributionIsWellFormed) {
  const RaceSimulator sim(stochastic_race());
  MonteCarloConfig cfg;
  cfg.scenarios = 1000;
  const auto r = run_monte_carlo(sim, 10, sim.race().drivers[10].strategy, cfg);
  const double total = std::accumulate(r.stats.histogram.begin(), r.stats.histogram.end(), 0.0);
  EXPECT_NEAR(total, 1.0, 1e-12);
  EXPECT_GE(r.stats.mean_position, 1.0);
  EXPECT_LE(r.stats.mean_position, 21.0);
  EXPECT_LE(r.stats.p05_position, r.stats.p50_position);
  EXPECT_LE(r.stats.p50_position, r.stats.p95_position);
  // 5% DNF probability: observed rate should be in a loose band.
  EXPECT_GT(r.stats.dnf_rate, 0.02);
  EXPECT_LT(r.stats.dnf_rate, 0.09);
  // Randomness actually produces a spread of outcomes.
  EXPECT_GT(r.stats.std_position, 0.5);
}

TEST(MonteCarlo, FasterDriverFinishesAheadOnAverage) {
  const RaceSimulator sim(stochastic_race());
  MonteCarloConfig cfg;
  cfg.scenarios = 1000;
  const auto stats = run_field_monte_carlo(sim, cfg);
  ASSERT_EQ(stats.size(), 20u);
  // Driver 0 is the fastest and starts on pole; driver 19 is slowest and last.
  EXPECT_LT(stats[0].mean_position, stats[10].mean_position);
  EXPECT_LT(stats[10].mean_position, stats[19].mean_position);
}

TEST(MonteCarlo, MatchedScenariosShareSafetyCars) {
  // Two strategies on the same scenario id face the same safety-car timeline.
  auto race = stochastic_race();
  const RaceSimulator sim(race);
  auto a = sim.default_strategies();
  auto b = a;
  b[0] = one_stop(kSoft, 12, kHard);
  const KeyedRng rng(4);
  for (std::uint64_t s = 0; s < 100; ++s) {
    EXPECT_EQ(sim.run(a, rng, s, {}).safety_car_laps, sim.run(b, rng, s, {}).safety_car_laps);
  }
}

TEST(MonteCarlo, RejectsBadConfig) {
  const RaceSimulator sim(stochastic_race());
  MonteCarloConfig cfg;
  cfg.scenarios = 0;
  EXPECT_THROW(run_monte_carlo(sim, 0, sim.race().drivers[0].strategy, cfg), std::invalid_argument);
  cfg.scenarios = 10;
  EXPECT_THROW(run_monte_carlo(sim, 99, sim.race().drivers[0].strategy, cfg), std::invalid_argument);
}

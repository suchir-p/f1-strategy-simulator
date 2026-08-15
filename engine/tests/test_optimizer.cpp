#include <gtest/gtest.h>

#include <algorithm>
#include <set>

#include "f1sim/optimizer.hpp"
#include "f1sim/strategy.hpp"
#include "test_helpers.hpp"

using namespace f1sim;
using namespace f1sim::testing;

TEST(Candidates, AllValidAndUnique) {
  const auto race = make_race(4, 30);
  SearchSpace space;
  space.min_stint_laps = 5;
  space.max_candidates = 10000;
  const auto candidates = generate_candidates(race, space);
  ASSERT_FALSE(candidates.empty());
  std::set<std::string> labels;
  for (const auto& c : candidates) {
    EXPECT_TRUE(validate_strategy(c, race, space.min_stint_laps).ok) << format_strategy(c, race);
    EXPECT_TRUE(labels.insert(format_strategy(c, race)).second) << "duplicate";
  }
}

TEST(Candidates, OneStopCountMatchesFormula) {
  const auto race = make_race(4, 30);
  SearchSpace space;
  space.two_stop = false;
  space.min_stint_laps = 5;  // Pit laps 5..25 -> 21 options.
  // 3 compounds -> 6 ordered pairs of different compounds.
  EXPECT_EQ(generate_candidates(race, space).size(), 6u * 21u);
  space.lap_step = 5;  // 5, 10, 15, 20, 25.
  EXPECT_EQ(generate_candidates(race, space).size(), 6u * 5u);
  space.lap_step = 1;
  space.window_start = 10;
  space.window_end = 14;
  EXPECT_EQ(generate_candidates(race, space).size(), 6u * 5u);
}

TEST(Candidates, RespectsObservedStintLimits) {
  auto race = make_race(4, 30);
  race.compounds[kSoft].max_stint_laps = 8;  // Softs only observed for 8 laps.
  SearchSpace space;
  space.two_stop = false;
  space.min_stint_laps = 5;
  space.stint_limit_margin = 2;
  for (const auto& c : generate_candidates(race, space)) {
    for (const auto& stint : to_stints(c, race.total_laps)) {
      if (stint.compound_index == kSoft) EXPECT_LE(stint.length(), 10) << format_strategy(c, race);
    }
  }
  // Soft-first one-stops with pit laps 5..10 survive; longer soft stints don't.
  const auto limited = generate_candidates(race, space);
  space.respect_stint_limits = false;
  EXPECT_LT(limited.size(), generate_candidates(race, space).size());
}

TEST(Candidates, CapIsEnforced) {
  const auto race = make_race(4, 50);
  SearchSpace space;
  space.max_candidates = 50;
  EXPECT_THROW(generate_candidates(race, space), std::invalid_argument);
}

TEST(Optimizer, RanksByExpectedPositionWithPairedIntervals) {
  auto race = make_race(10, 30);
  race.safety_car.start_probability_per_lap = 0.03;
  const RaceSimulator sim(race);
  MonteCarloConfig cfg;
  cfg.scenarios = 300;
  const int target = 4;
  // A sensible one-stop, a plausible alternative, and a clearly bad strategy
  // (three stints with an early, pointless stop).
  const std::vector<Strategy> candidates = {
      one_stop(kMedium, 14, kHard),
      one_stop(kSoft, 10, kHard),
      Strategy{kHard, {{3, kHard}, {6, kMedium}}},
  };
  const auto res = evaluate_candidates(sim, target, candidates, cfg);
  ASSERT_EQ(res.ranked.size(), 3u);
  EXPECT_EQ(res.candidates_evaluated, 3);
  for (std::size_t i = 1; i < res.ranked.size(); ++i) {
    EXPECT_LE(res.ranked[i - 1].stats.mean_position, res.ranked[i].stats.mean_position);
    EXPECT_EQ(res.ranked[i].rank, static_cast<int>(i) + 1);
  }
  EXPECT_EQ(res.ranked[0].diff_vs_best, 0.0);
  EXPECT_FALSE(res.ranked[0].inconclusive_vs_best);
  for (const auto& c : res.ranked) {
    EXPECT_LE(c.diff_ci_low, c.diff_vs_best);
    EXPECT_GE(c.diff_ci_high, c.diff_vs_best);
  }
  // The two-extra-stop strategy is last and clearly worse.
  EXPECT_EQ(res.ranked.back().label, "H-3-H-6-M");
  for (const auto& c : res.ranked) EXPECT_TRUE(c.notes.empty());  // All race-fitted.
  EXPECT_FALSE(res.ranked.back().inconclusive_vs_best);
  // Paired difference equals the difference of means on matched scenarios.
  EXPECT_NEAR(res.ranked.back().diff_vs_best,
              res.ranked.back().stats.mean_position - res.ranked[0].stats.mean_position, 1e-9);
}

TEST(Optimizer, GridSearchBeatsOrMatchesTheDefaultStrategy) {
  auto race = make_race(10, 30);
  const RaceSimulator sim(race);
  MonteCarloConfig cfg;
  cfg.scenarios = 200;
  SearchSpace space;
  space.two_stop = false;
  space.min_stint_laps = 6;
  auto candidates = generate_candidates(race, space);
  const Strategy def = race.drivers[5].strategy;  // M-15-H, inside the space.
  ASSERT_NE(std::find(candidates.begin(), candidates.end(), def), candidates.end());
  const auto res = evaluate_candidates(sim, 5, candidates, cfg);
  const auto default_mean = run_monte_carlo(sim, 5, def, cfg).stats.mean_position;
  EXPECT_LE(res.ranked[0].stats.mean_position, default_mean);
}

TEST(Optimizer, RejectsInvalidCandidates) {
  const RaceSimulator sim(make_race(4, 20));
  MonteCarloConfig cfg;
  cfg.scenarios = 10;
  EXPECT_THROW(evaluate_candidates(sim, 0, {Strategy{kMedium, {}}}, cfg), std::invalid_argument);
  EXPECT_THROW(evaluate_candidates(sim, 0, {}, cfg), std::invalid_argument);
}

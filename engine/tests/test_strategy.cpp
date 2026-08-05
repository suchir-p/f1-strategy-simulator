#include <gtest/gtest.h>

#include "f1sim/config_io.hpp"
#include "f1sim/strategy.hpp"
#include "test_helpers.hpp"

using namespace f1sim;
using namespace f1sim::testing;

TEST(Strategy, ValidOneAndTwoStop) {
  const auto race = make_race(4, 20);
  EXPECT_TRUE(validate_strategy(one_stop(kMedium, 10, kHard), race).ok);
  EXPECT_TRUE(validate_strategy(Strategy{kSoft, {{5, kMedium}, {12, kHard}}}, race).ok);
  // Two stints on the same compound plus one other still satisfies the rule.
  EXPECT_TRUE(validate_strategy(Strategy{kMedium, {{7, kMedium}, {14, kHard}}}, race).ok);
}

TEST(Strategy, RejectsSingleCompound) {
  const auto race = make_race(4, 20);
  EXPECT_FALSE(validate_strategy(Strategy{kMedium, {}}, race).ok);
  EXPECT_FALSE(validate_strategy(one_stop(kHard, 10, kHard), race).ok);
}

TEST(Strategy, RejectsBadLaps) {
  const auto race = make_race(4, 20);
  EXPECT_FALSE(validate_strategy(one_stop(kMedium, 0, kHard), race).ok);
  EXPECT_FALSE(validate_strategy(one_stop(kMedium, 20, kHard), race).ok);  // Final lap.
  EXPECT_FALSE(validate_strategy(Strategy{kSoft, {{12, kMedium}, {8, kHard}}}, race).ok);
  EXPECT_FALSE(validate_strategy(Strategy{kSoft, {{8, kMedium}, {8, kHard}}}, race).ok);
}

TEST(Strategy, RejectsUnknownCompound) {
  const auto race = make_race(4, 20);
  EXPECT_FALSE(validate_strategy(one_stop(7, 10, kHard), race).ok);
  EXPECT_FALSE(validate_strategy(one_stop(kMedium, 10, -1), race).ok);
}

TEST(Strategy, MinimumStintLength) {
  const auto race = make_race(4, 20);
  EXPECT_TRUE(validate_strategy(one_stop(kMedium, 3, kHard), race, 3).ok);
  EXPECT_FALSE(validate_strategy(one_stop(kMedium, 3, kHard), race, 4).ok);
  EXPECT_FALSE(validate_strategy(one_stop(kMedium, 18, kHard), race, 3).ok);  // 2-lap final stint.
}

TEST(Strategy, StintsCoverTheRace) {
  const auto stints = to_stints(Strategy{kSoft, {{5, kMedium}, {12, kHard}}}, 20);
  ASSERT_EQ(stints.size(), 3u);
  EXPECT_EQ(stints[0].start_lap, 1);
  EXPECT_EQ(stints[0].end_lap, 5);
  EXPECT_EQ(stints[1].start_lap, 6);
  EXPECT_EQ(stints[2].end_lap, 20);
  int total = 0;
  for (const auto& s : stints) total += s.length();
  EXPECT_EQ(total, 20);
}

TEST(Strategy, TextRoundTrip) {
  const auto race = make_race(4, 20);
  const Strategy s{kSoft, {{5, kMedium}, {12, kHard}}};
  EXPECT_EQ(format_strategy(s, race), "S-5-M-12-H");
  EXPECT_EQ(parse_strategy("S-5-M-12-H", race), s);
  EXPECT_EQ(parse_strategy("m-10-h", race), one_stop(kMedium, 10, kHard));
  EXPECT_THROW(parse_strategy("M-10", race), std::invalid_argument);
  EXPECT_THROW(parse_strategy("X-10-H", race), std::invalid_argument);
}

TEST(Strategy, RaceValidationCatchesBadDrivers) {
  auto race = make_race(4, 20);
  EXPECT_TRUE(validate_race(race).ok);
  race.drivers[1].grid_position = 1;  // Duplicate grid slot.
  EXPECT_FALSE(validate_race(race).ok);
  race = make_race(4, 20);
  race.drivers[2].strategy = Strategy{kMedium, {}};
  EXPECT_FALSE(validate_race(race).ok);
}

TEST(StrategyNotes, FlagsLowConfidenceAndLongStints) {
  auto race = make_race(4, 30);
  // Race-fitted compounds with no stint limit: nothing to report.
  EXPECT_TRUE(strategy_notes(one_stop(kMedium, 15, kHard), race).empty());

  race.compounds[kSoft].confidence = TireConfidence::Pooled;
  race.compounds[kHard].max_stint_laps = 12;
  const auto notes = strategy_notes(one_stop(kSoft, 10, kHard), race);  // 20-lap hard stint.
  ASSERT_EQ(notes.size(), 2u);
  EXPECT_NE(notes[0].find("other races"), std::string::npos);
  EXPECT_NE(notes[1].find("longer than the 12 laps observed"), std::string::npos);
}

TEST(ConfigIo, JsonRoundTripAndExampleConfig) {
  const auto race = make_race(4, 20);
  const auto back = race_from_json(race_to_json(race));
  EXPECT_EQ(back.total_laps, race.total_laps);
  ASSERT_EQ(back.drivers.size(), race.drivers.size());
  EXPECT_EQ(back.drivers[3].strategy, race.drivers[3].strategy);
  EXPECT_EQ(back.compounds[kSoft].absolute_compound, 4);
  auto pooled = race;
  pooled.compounds[kSoft].confidence = TireConfidence::Fallback;
  EXPECT_EQ(race_from_json(race_to_json(pooled)).compounds[kSoft].confidence, TireConfidence::Fallback);
  auto bad = race_to_json(race);
  bad["compounds"][0]["confidence"] = "guess";
  EXPECT_THROW(race_from_json(bad), std::invalid_argument);

  const auto example = load_race(F1SIM_EXAMPLE_CONFIG);
  EXPECT_EQ(example.drivers.size(), 20u);
  EXPECT_TRUE(validate_race(example).ok);
}

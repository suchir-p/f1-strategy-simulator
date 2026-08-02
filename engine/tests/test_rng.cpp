#include <gtest/gtest.h>

#include <cmath>
#include <set>

#include "f1sim/rng.hpp"

using namespace f1sim;

TEST(KeyedRng, SameKeySameValue) {
  const KeyedRng a(7), b(7);
  const RandomKey k{12, 30, 4, RandomEvent::LapNoise, 0};
  EXPECT_EQ(a.bits(k), b.bits(k));
  EXPECT_EQ(a.uniform(k), a.uniform(k));
  EXPECT_EQ(a.normal(k), b.normal(k));
}

TEST(KeyedRng, EveryKeyFieldMatters) {
  const KeyedRng rng(7);
  const RandomKey base{12, 30, 4, RandomEvent::LapNoise, 0};
  std::set<std::uint64_t> seen{rng.bits(base)};
  auto vary = [&](RandomKey k) { EXPECT_TRUE(seen.insert(rng.bits(k)).second); };
  RandomKey k = base; k.scenario = 13; vary(k);
  k = base; k.lap = 31; vary(k);
  k = base; k.driver = 5; vary(k);
  k = base; k.event = RandomEvent::Overtake; vary(k);
  k = base; k.draw = 1; vary(k);
  // Swapped fields must not collide.
  k = base; k.lap = 4; k.driver = 30; vary(k);
  EXPECT_NE(KeyedRng(8).bits(base), rng.bits(base));
}

TEST(KeyedRng, UniformMomentsAndRange) {
  const KeyedRng rng(1);
  const int n = 200000;
  double sum = 0, sq = 0;
  for (int i = 0; i < n; ++i) {
    const double u = rng.uniform({static_cast<std::uint64_t>(i), 0, 0, RandomEvent::LapNoise, 0});
    ASSERT_GT(u, 0.0);
    ASSERT_LT(u, 1.0);
    sum += u;
    sq += u * u;
  }
  const double mean = sum / n;
  EXPECT_NEAR(mean, 0.5, 0.005);
  EXPECT_NEAR(sq / n - mean * mean, 1.0 / 12.0, 0.002);
}

TEST(KeyedRng, NormalMoments) {
  const KeyedRng rng(2);
  const int n = 200000;
  double sum = 0, sq = 0;
  for (int i = 0; i < n; ++i) {
    const double z = rng.normal({0, static_cast<std::uint32_t>(i), 3, RandomEvent::LapNoise, 0});
    sum += z;
    sq += z * z;
  }
  EXPECT_NEAR(sum / n, 0.0, 0.01);
  EXPECT_NEAR(sq / n, 1.0, 0.02);
}

TEST(KeyedRng, UniformIntCoversRangeInclusive) {
  const KeyedRng rng(3);
  std::set<int> values;
  for (std::uint32_t i = 0; i < 5000; ++i) {
    const int v = rng.uniform_int({0, i, 0, RandomEvent::SafetyCarLength, 0}, 2, 5);
    ASSERT_GE(v, 2);
    ASSERT_LE(v, 5);
    values.insert(v);
  }
  EXPECT_EQ(values.size(), 4u);
  EXPECT_EQ(rng.uniform_int({}, 3, 3), 3);
}

// Values from the original implementation, which re-hashed the whole key on
// every draw. Caching partial hashes must not change a single bit.
TEST(KeyedRng, MatchesOriginalHash) {
  auto k = [](std::uint64_t sc, std::uint32_t lap, std::uint32_t drv, RandomEvent ev,
              std::uint32_t draw) { return RandomKey{sc, lap, drv, ev, draw}; };
  EXPECT_EQ(KeyedRng(42).bits(k(0, 1, 3, RandomEvent::LapNoise, 0)), 0x523db332a2f922edULL);
  EXPECT_EQ(KeyedRng(7).bits(k(123456, 53, 19, RandomEvent::Overtake, 1)), 0x9d1753c954c14667ULL);
  EXPECT_EQ(KeyedRng(2024).bits(k(999, 0, kNoDriver, RandomEvent::SafetyCarStart, 0)),
            0x1c3bf6eb5abc3081ULL);
  // Fields beyond the precomputed table fall back to computing the mix.
  EXPECT_EQ(KeyedRng(1).bits(k(5, 300, 1000, RandomEvent::Retirement, 7)), 0x14d2e3775f0b2330ULL);
}

TEST(KeyedRng, StreamsMatchFullKeys) {
  const KeyedRng rng(99);
  for (std::uint64_t sc : {0ull, 17ull, 1ull << 40}) {
    const auto s = rng.scenario(sc);
    for (int lap : {0, 1, 70, 400}) {
      for (std::uint32_t d : {0u, 19u, kNoDriver}) {
        for (auto ev : {RandomEvent::LapNoise, RandomEvent::Overtake, RandomEvent::RacePace}) {
          const auto e = s.lap(lap).event(d, ev);
          RandomKey key{sc, static_cast<std::uint32_t>(lap), d, ev, 0};
          EXPECT_EQ(e.bits(0), rng.bits(key));
          EXPECT_EQ(e.uniform(), rng.uniform(key));
          EXPECT_EQ(e.normal(), rng.normal(key));
          EXPECT_EQ(e.uniform_int(1, 53), rng.uniform_int(key, 1, 53));
          key.draw = 3;
          EXPECT_EQ(e.bits(3), rng.bits(key));
          EXPECT_EQ(e.normal(3), rng.normal(key));
        }
      }
    }
  }
}

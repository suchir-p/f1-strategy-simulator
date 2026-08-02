#pragma once
// Event-keyed, stateless random numbers.
//
// Every random draw is a pure function of (master seed, scenario, lap, driver,
// event type, draw index). There is no generator state to advance, so:
//   * the same key gives the same value no matter which strategy is being
//     simulated or in what order events are evaluated (matched scenarios), and
//   * results are independent of OpenMP thread scheduling.
// Draws for events that never happen under a given strategy are simply never
// requested; they cannot shift any other draw.

#include <cstdint>

namespace f1sim {

enum class RandomEvent : std::uint32_t {
  RacePace = 1,       // Persistent per-driver offset for a scenario.
  LapNoise = 2,       // Per-driver, per-lap noise.
  Overtake = 3,       // Overtake attempt outcome.
  SafetyCarStart = 4, // Safety-car deployment check for a lap.
  SafetyCarLength = 5,// Safety-car duration.
  Retirement = 6,     // Whether a driver retires in this scenario.
  RetirementLap = 7,  // Which lap the retirement happens on.
  VscStart = 8,       // Virtual-safety-car deployment check for a lap.
  VscLength = 9,      // Virtual-safety-car duration.
};

inline constexpr std::uint32_t kNoDriver = 0xFFFFu;

struct RandomKey {
  std::uint64_t scenario = 0;
  std::uint32_t lap = 0;
  std::uint32_t driver = kNoDriver;
  RandomEvent event = RandomEvent::LapNoise;
  std::uint32_t draw = 0;
};

// The hash absorbs the key fields in a fixed order (seed, scenario, lap,
// driver, event, draw), so a partial hash can be computed once and reused for
// every draw that shares the leading fields. Hot loops use this to avoid
// re-hashing the same scenario/lap/driver for each draw; values are
// bit-identical to KeyedRng::bits on the full key.
class EventStream {
 public:
  std::uint64_t bits(std::uint32_t draw) const;
  double uniform(std::uint32_t draw = 0) const;
  double normal(std::uint32_t k = 0) const;  // Uses draws 2k and 2k+1.
  int uniform_int(int lo, int hi, std::uint32_t draw = 0) const;

 private:
  friend class LapStream;
  explicit EventStream(std::uint64_t h) : h_(h) {}
  std::uint64_t h_;
};

class LapStream {
 public:
  EventStream event(std::uint32_t driver, RandomEvent event) const;

 private:
  friend class ScenarioStream;
  explicit LapStream(std::uint64_t h) : h_(h) {}
  std::uint64_t h_;
};

class ScenarioStream {
 public:
  LapStream lap(int lap) const;
  EventStream event(int lap, std::uint32_t driver, RandomEvent event) const {
    return this->lap(lap).event(driver, event);
  }

 private:
  friend class KeyedRng;
  explicit ScenarioStream(std::uint64_t h) : h_(h) {}
  std::uint64_t h_;
};

class KeyedRng {
 public:
  explicit KeyedRng(std::uint64_t master_seed);

  // Partial hash after absorbing the seed and the scenario.
  ScenarioStream scenario(std::uint64_t scenario) const;

  // 64 uniformly distributed bits for the key.
  std::uint64_t bits(const RandomKey& key) const;
  // Uniform in the open interval (0, 1).
  double uniform(const RandomKey& key) const;
  // Standard normal via Box-Muller; consumes draws 2*k and 2*k+1 of the key.
  double normal(const RandomKey& key) const;
  // Uniform integer in [lo, hi] (inclusive).
  int uniform_int(const RandomKey& key, int lo, int hi) const;

  std::uint64_t seed() const { return seed_; }

 private:
  std::uint64_t seed_;
  std::uint64_t seed_hash_;  // mix64(seed), computed once.
};

}  // namespace f1sim

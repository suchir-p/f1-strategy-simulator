#include "f1sim/rng.hpp"

#include <array>
#include <cmath>
#include <numbers>

namespace f1sim {
namespace {

// SplitMix64 finalizer: a fast, well-tested 64-bit bijective mixer.
constexpr std::uint64_t mix64(std::uint64_t z) {
  z += 0x9E3779B97F4A7C15ULL;
  z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
  z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
  return z ^ (z >> 31);
}

// mix64 of small field values (laps, drivers, event types, draw indices),
// computed at compile time so absorbing one costs a single mix, not two.
constexpr std::size_t kTable = 256;
constexpr std::array<std::uint64_t, kTable> kMixed = [] {
  std::array<std::uint64_t, kTable> t{};
  for (std::size_t i = 0; i < kTable; ++i) t[i] = mix64(i);
  return t;
}();

inline std::uint64_t mixed(std::uint64_t v) { return v < kTable ? kMixed[v] : mix64(v); }

// Absorbs one key field into the running hash. Mixing after every field keeps
// keys like (lap=1, driver=2) and (lap=2, driver=1) far apart.
inline std::uint64_t absorb(std::uint64_t h, std::uint64_t v) { return mix64(h ^ mixed(v)); }

inline double to_unit(std::uint64_t bits) {
  // Top 53 bits -> [0, 1), then shift by half an ulp so 0 is never returned.
  return (static_cast<double>(bits >> 11) + 0.5) * (1.0 / 9007199254740992.0);
}

inline int to_int(std::uint64_t bits, int lo, int hi) {
  if (hi <= lo) return lo;
  const auto span = static_cast<std::uint64_t>(hi - lo) + 1;
  // Modulo reduction; for spans this small the bias is ~span / 2^64.
  return lo + static_cast<int>(bits % span);
}

inline double box_muller(double u1, double u2) {
  return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * std::numbers::pi * u2);
}

}  // namespace

// ---- streams -------------------------------------------------------------------------

std::uint64_t EventStream::bits(std::uint32_t draw) const { return absorb(h_, draw); }

double EventStream::uniform(std::uint32_t draw) const { return to_unit(bits(draw)); }

double EventStream::normal(std::uint32_t k) const {
  return box_muller(uniform(2 * k), uniform(2 * k + 1));
}

int EventStream::uniform_int(int lo, int hi, std::uint32_t draw) const {
  return to_int(bits(draw), lo, hi);
}

EventStream LapStream::event(std::uint32_t driver, RandomEvent event) const {
  return EventStream(absorb(absorb(h_, driver), static_cast<std::uint64_t>(event)));
}

LapStream ScenarioStream::lap(int lap) const {
  return LapStream(absorb(h_, static_cast<std::uint32_t>(lap)));
}

// ---- KeyedRng: the same hash, from the full key -----------------------------------------

KeyedRng::KeyedRng(std::uint64_t master_seed) : seed_(master_seed), seed_hash_(mix64(master_seed)) {}

ScenarioStream KeyedRng::scenario(std::uint64_t scenario) const {
  return ScenarioStream(absorb(seed_hash_, scenario));
}

std::uint64_t KeyedRng::bits(const RandomKey& key) const {
  return scenario(key.scenario)
      .event(static_cast<int>(key.lap), key.driver, key.event)
      .bits(key.draw);
}

double KeyedRng::uniform(const RandomKey& key) const { return to_unit(bits(key)); }

double KeyedRng::normal(const RandomKey& key) const {
  const EventStream e =
      scenario(key.scenario).event(static_cast<int>(key.lap), key.driver, key.event);
  return e.normal(key.draw);
}

int KeyedRng::uniform_int(const RandomKey& key, int lo, int hi) const {
  return to_int(bits(key), lo, hi);
}

}  // namespace f1sim

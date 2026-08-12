#include "f1sim/monte_carlo.hpp"

#include <omp.h>

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace f1sim {
namespace {

int percentile(std::vector<int> sorted_values, double q) {
  if (sorted_values.empty()) return 0;
  std::sort(sorted_values.begin(), sorted_values.end());
  // Nearest-rank method.
  const auto rank = static_cast<std::size_t>(
      std::ceil(q * static_cast<double>(sorted_values.size())));
  const std::size_t idx = rank == 0 ? 0 : std::min(rank - 1, sorted_values.size() - 1);
  return sorted_values[idx];
}

void check_config(const RaceSimulator& sim, const MonteCarloConfig& config) {
  if (config.scenarios <= 0) throw std::invalid_argument("scenarios must be positive");
  if (config.threads < 0) throw std::invalid_argument("threads must be >= 0");
  if (sim.race().drivers.empty()) throw std::invalid_argument("race has no drivers");
}

int thread_count(const MonteCarloConfig& config) {
  return config.threads > 0 ? config.threads : omp_get_max_threads();
}

}  // namespace

OutcomeStats summarize(const std::vector<int>& scored_positions,
                       const std::vector<double>& race_times, int field_size) {
  OutcomeStats s;
  const int n = static_cast<int>(scored_positions.size());
  s.scenarios = n;
  s.histogram.assign(field_size + 1, 0.0);
  if (n == 0) return s;

  const int dnf_score = field_size + 1;
  double sum = 0.0;
  double time_sum = 0.0;
  int finishers = 0;
  for (int i = 0; i < n; ++i) {
    const int p = scored_positions[i];
    sum += p;
    s.histogram[p - 1] += 1.0;
    if (p == dnf_score) {
      s.dnf_rate += 1.0;
    } else {
      if (p == 1) s.p_win += 1.0;
      if (p <= 3) s.p_podium += 1.0;
      if (p <= 10) s.p_points += 1.0;
      if (!race_times.empty()) {
        time_sum += race_times[i];
        ++finishers;
      }
    }
  }
  s.mean_position = sum / n;
  double sq = 0.0;
  for (int p : scored_positions) sq += (p - s.mean_position) * (p - s.mean_position);
  s.std_position = n > 1 ? std::sqrt(sq / (n - 1)) : 0.0;
  s.p05_position = percentile(scored_positions, 0.05);
  s.p50_position = percentile(scored_positions, 0.50);
  s.p95_position = percentile(scored_positions, 0.95);
  s.p_win /= n;
  s.p_podium /= n;
  s.p_points /= n;
  s.dnf_rate /= n;
  s.mean_race_time = finishers > 0 ? time_sum / finishers : 0.0;

  std::vector<int> finished;
  finished.reserve(n);
  for (int p : scored_positions) {
    if (p != dnf_score) finished.push_back(p);
  }
  s.finish_probability = static_cast<double>(finished.size()) / n;
  if (!finished.empty()) {
    double fsum = 0.0;
    for (int p : finished) fsum += p;
    s.mean_position_if_finished = fsum / static_cast<double>(finished.size());
    s.p05_if_finished = percentile(finished, 0.05);
    s.p50_if_finished = percentile(finished, 0.50);
    s.p95_if_finished = percentile(finished, 0.95);
  }
  for (double& h : s.histogram) h /= n;
  return s;
}

PairedDifference paired_difference(const std::vector<int>& a, const std::vector<int>& b) {
  if (a.size() != b.size() || a.empty()) {
    throw std::invalid_argument("paired_difference needs two equal-length, non-empty samples");
  }
  const auto n = static_cast<double>(a.size());
  double sum = 0.0;
  for (std::size_t s = 0; s < a.size(); ++s) sum += a[s] - b[s];
  const double mean = sum / n;
  double sq = 0.0;
  for (std::size_t s = 0; s < a.size(); ++s) {
    const double d = (a[s] - b[s]) - mean;
    sq += d * d;
  }
  const double sd = a.size() > 1 ? std::sqrt(sq / (n - 1)) : 0.0;
  const double half = 1.96 * sd / std::sqrt(n);
  return {mean, mean - half, mean + half};
}

MonteCarloResult run_monte_carlo(const RaceSimulator& sim, int target_driver,
                                 const Strategy& target_strategy,
                                 const MonteCarloConfig& config) {
  check_config(sim, config);
  const int field = static_cast<int>(sim.race().drivers.size());
  if (target_driver < 0 || target_driver >= field) {
    throw std::invalid_argument("target driver index out of range");
  }

  std::vector<Strategy> base = sim.default_strategies();
  base[target_driver] = target_strategy;
  const KeyedRng rng(config.seed);

  MonteCarloResult out;
  out.scored_positions.assign(config.scenarios, 0);
  out.race_times.assign(config.scenarios, 0.0);

  // One level of parallelism: independent scenarios. Each iteration only
  // reads shared, immutable inputs and writes to its own output slot.
#pragma omp parallel for schedule(static) num_threads(thread_count(config))
  for (int s = 0; s < config.scenarios; ++s) {
    const RaceResult r =
        sim.run(base, rng, config.first_scenario + static_cast<std::uint64_t>(s), config.options);
    const auto& dr = r.classification[r.position_by_driver[target_driver] - 1];
    out.scored_positions[s] = dr.status == DriverStatus::Retired ? field + 1 : dr.position;
    out.race_times[s] = dr.race_time;
  }

  out.stats = summarize(out.scored_positions, out.race_times, field);
  return out;
}

std::vector<OutcomeStats> run_field_monte_carlo(const RaceSimulator& sim,
                                                const MonteCarloConfig& config) {
  check_config(sim, config);
  const int field = static_cast<int>(sim.race().drivers.size());
  const std::vector<Strategy> base = sim.default_strategies();
  const KeyedRng rng(config.seed);

  // positions[d * scenarios + s], times likewise.
  std::vector<int> positions(static_cast<std::size_t>(field) * config.scenarios);
  std::vector<double> times(positions.size());

#pragma omp parallel for schedule(static) num_threads(thread_count(config))
  for (int s = 0; s < config.scenarios; ++s) {
    const RaceResult r =
        sim.run(base, rng, config.first_scenario + static_cast<std::uint64_t>(s), config.options);
    for (const auto& dr : r.classification) {
      const std::size_t idx = static_cast<std::size_t>(dr.driver_index) * config.scenarios + s;
      positions[idx] = dr.status == DriverStatus::Retired ? field + 1 : dr.position;
      times[idx] = dr.race_time;
    }
  }

  std::vector<OutcomeStats> stats;
  stats.reserve(field);
  for (int d = 0; d < field; ++d) {
    const auto begin = static_cast<std::ptrdiff_t>(d) * config.scenarios;
    std::vector<int> p(positions.begin() + begin, positions.begin() + begin + config.scenarios);
    std::vector<double> t(times.begin() + begin, times.begin() + begin + config.scenarios);
    stats.push_back(summarize(p, t, field));
  }
  return stats;
}

}  // namespace f1sim

#include "f1sim/optimizer.hpp"

#include <omp.h>

#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>

#include "f1sim/strategy.hpp"

namespace f1sim {
namespace {

std::vector<int> pit_laps(const RaceConfig& race, const SearchSpace& space) {
  const int lo = std::max({1, space.min_stint_laps, space.window_start});
  int hi = race.total_laps - std::max(1, space.min_stint_laps);
  if (space.window_end > 0) hi = std::min(hi, space.window_end);
  std::vector<int> laps;
  for (int lap = lo; lap <= hi; lap += std::max(1, space.lap_step)) laps.push_back(lap);
  return laps;
}

}  // namespace

std::vector<Strategy> generate_candidates(const RaceConfig& race, const SearchSpace& space) {
  std::vector<int> compounds = space.compound_indices;
  if (compounds.empty()) {
    compounds.resize(race.compounds.size());
    std::iota(compounds.begin(), compounds.end(), 0);
  }
  for (int c : compounds) {
    if (c < 0 || c >= static_cast<int>(race.compounds.size())) {
      throw std::invalid_argument("search space compound index out of range");
    }
  }
  const std::vector<int> laps = pit_laps(race, space);

  std::vector<Strategy> out;
  auto within_limits = [&](const Strategy& s) {
    if (!space.respect_stint_limits) return true;
    for (const auto& stint : to_stints(s, race.total_laps)) {
      const int limit = race.compounds[stint.compound_index].max_stint_laps;
      if (limit > 0 && stint.length() > limit + space.stint_limit_margin) return false;
    }
    return true;
  };
  auto add = [&](Strategy s) {
    if (!validate_strategy(s, race, space.min_stint_laps).ok || !within_limits(s)) return;
    if (static_cast<int>(out.size()) >= space.max_candidates) {
      throw std::invalid_argument(
          "search space exceeds max_candidates (" + std::to_string(space.max_candidates) +
          "); narrow the pit window, increase lap_step, or raise the cap");
    }
    out.push_back(std::move(s));
  };

  if (space.one_stop) {
    for (int c0 : compounds)
      for (int c1 : compounds)
        for (int l1 : laps) add(Strategy{c0, {{l1, c1}}});
  }
  if (space.two_stop) {
    for (int c0 : compounds)
      for (int c1 : compounds)
        for (int c2 : compounds)
          for (int l1 : laps)
            for (int l2 : laps)
              if (l2 > l1) add(Strategy{c0, {{l1, c1}, {l2, c2}}});
  }
  // Validation already rejects duplicates structurally (each tuple is unique).
  return out;
}

OptimizerResult evaluate_candidates(const RaceSimulator& sim, int target_driver,
                                    const std::vector<Strategy>& candidates,
                                    const MonteCarloConfig& config) {
  if (candidates.empty()) throw std::invalid_argument("no candidate strategies to evaluate");
  for (const auto& c : candidates) {
    if (auto v = validate_strategy(c, sim.race()); !v.ok) {
      throw std::invalid_argument("invalid candidate " + format_strategy(c, sim.race()) + ": " +
                                  v.error);
    }
  }

  if (config.scenarios <= 0) throw std::invalid_argument("scenarios must be positive");
  const int field = static_cast<int>(sim.race().drivers.size());
  if (target_driver < 0 || target_driver >= field) {
    throw std::invalid_argument("target driver index out of range");
  }
  const int n = config.scenarios;
  const std::size_t num = candidates.size();
  const int threads = config.threads > 0 ? config.threads : omp_get_max_threads();
  const KeyedRng rng(config.seed);

  // Safety cars, retirements and race-day pace don't depend on the strategy,
  // so each scenario's events are drawn once and shared by every candidate.
  std::vector<ScenarioEvents> events(static_cast<std::size_t>(n));
#pragma omp parallel for schedule(static) num_threads(threads)
  for (int s = 0; s < n; ++s) {
    events[s] = build_scenario_events(sim.race(), rng,
                                      config.first_scenario + static_cast<std::uint64_t>(s),
                                      config.options);
  }

  // One parallel loop over every (candidate, scenario) pair. Splitting the
  // work per candidate instead would mean a fork/join per candidate with only
  // n / threads races per thread in between, which stops scaling once threads
  // approach n. Each pair writes only its own slot, so results don't depend on
  // the schedule or thread count.
  std::vector<int> scored(num * static_cast<std::size_t>(n));
  std::vector<double> times(scored.size());
#pragma omp parallel num_threads(threads)
  {
    std::vector<Strategy> strategies = sim.default_strategies();
    std::size_t loaded = num;  // Candidate currently in strategies[target].
#pragma omp for schedule(static)
    for (std::size_t i = 0; i < scored.size(); ++i) {
      const std::size_t c = i / static_cast<std::size_t>(n);
      const int s = static_cast<int>(i % static_cast<std::size_t>(n));
      if (c != loaded) {
        strategies[target_driver] = candidates[c];
        loaded = c;
      }
      const RaceResult r = sim.run(strategies, rng,
                                   config.first_scenario + static_cast<std::uint64_t>(s),
                                   config.options, events[s]);
      const auto& dr = r.classification[r.position_by_driver[target_driver] - 1];
      scored[i] = dr.status == DriverStatus::Retired ? field + 1 : dr.position;
      times[i] = dr.race_time;
    }
  }

  std::vector<MonteCarloResult> runs(num);
  for (std::size_t c = 0; c < num; ++c) {
    const auto begin = static_cast<std::ptrdiff_t>(c * static_cast<std::size_t>(n));
    runs[c].scored_positions.assign(scored.begin() + begin, scored.begin() + begin + n);
    runs[c].race_times.assign(times.begin() + begin, times.begin() + begin + n);
    runs[c].stats = summarize(runs[c].scored_positions, runs[c].race_times, field);
  }

  std::vector<std::size_t> order(candidates.size());
  std::iota(order.begin(), order.end(), 0);
  std::stable_sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) {
    return runs[a].stats.mean_position < runs[b].stats.mean_position;
  });

  const auto& best = runs[order.front()].scored_positions;

  OptimizerResult result;
  result.candidates_evaluated = static_cast<int>(candidates.size());
  result.scenarios_per_candidate = n;
  result.ranked.reserve(candidates.size());
  for (std::size_t r = 0; r < order.size(); ++r) {
    const std::size_t i = order[r];
    CandidateResult cr;
    cr.rank = static_cast<int>(r) + 1;
    cr.strategy = candidates[i];
    cr.label = format_strategy(candidates[i], sim.race());
    cr.notes = strategy_notes(candidates[i], sim.race());
    cr.stats = runs[i].stats;
    cr.inconclusive_vs_best = false;  // The best candidate is the reference.

    if (r > 0) {
      // Paired differences on matched scenarios.
      const PairedDifference diff = paired_difference(runs[i].scored_positions, best);
      cr.diff_vs_best = diff.mean;
      cr.diff_ci_low = diff.ci_low;
      cr.diff_ci_high = diff.ci_high;
      cr.inconclusive_vs_best = cr.diff_ci_low <= 0.0;
    }
    result.ranked.push_back(std::move(cr));
  }
  return result;
}

OptimizerResult optimize_strategy(const RaceSimulator& sim, int target_driver,
                                  const SearchSpace& space, const MonteCarloConfig& config) {
  return evaluate_candidates(sim, target_driver, generate_candidates(sim.race(), space), config);
}

}  // namespace f1sim

#pragma once
// Exhaustive grid search over one- and two-stop strategies for one driver.
//
// Every candidate is evaluated on the same matched scenarios (same seed, same
// scenario ids), so differences between candidates come from the strategy,
// not from luck. Candidates are ranked by expected scored position, and each
// one reports a paired 95% interval for its difference from the top-ranked
// strategy; if that interval includes zero the gap is "inconclusive" at the
// chosen scenario budget.

#include <string>
#include <vector>

#include "f1sim/monte_carlo.hpp"

namespace f1sim {

struct SearchSpace {
  bool one_stop = true;
  bool two_stop = true;
  int min_stint_laps = 8;      // Shortest allowed stint.
  int lap_step = 1;            // Pit-lap granularity (2 = every other lap).
  int window_start = 0;        // Earliest pit lap considered (0 = no limit).
  int window_end = 0;          // Latest pit lap considered (0 = no limit).
  std::vector<int> compound_indices;  // Compounds to consider (empty = all).
  int max_candidates = 500;    // Hard cap; generation fails if exceeded.
  // Skip stints longer than a compound's max_stint_laps + margin, so the
  // search doesn't rely on the wear curve far outside the fitted data.
  bool respect_stint_limits = true;
  int stint_limit_margin = 3;
};

struct CandidateResult {
  int rank = 0;                // 1 = best.
  Strategy strategy;
  std::string label;           // e.g. "M-18-H".
  OutcomeStats stats;
  // Paired comparison with the top-ranked candidate (candidate - best;
  // positive means worse). Zero for the best candidate itself.
  double diff_vs_best = 0.0;
  double diff_ci_low = 0.0;
  double diff_ci_high = 0.0;
  bool inconclusive_vs_best = true;
  // Reasons to trust this candidate less: stints on low-confidence tire
  // estimates or longer than the data the wear curve was fitted on.
  std::vector<std::string> notes;
};

struct OptimizerResult {
  int candidates_evaluated = 0;
  int scenarios_per_candidate = 0;
  std::vector<CandidateResult> ranked;  // Best first.
};

// Enumerates valid candidate strategies. Throws std::invalid_argument if the
// space would exceed `max_candidates` (tighten the window or raise lap_step).
std::vector<Strategy> generate_candidates(const RaceConfig& race, const SearchSpace& space);

// Evaluates every candidate for `target_driver` and ranks them.
OptimizerResult optimize_strategy(const RaceSimulator& sim, int target_driver,
                                  const SearchSpace& space, const MonteCarloConfig& config);

// Same, for an explicit candidate list (e.g. strategies the user built).
OptimizerResult evaluate_candidates(const RaceSimulator& sim, int target_driver,
                                    const std::vector<Strategy>& candidates,
                                    const MonteCarloConfig& config);

}  // namespace f1sim

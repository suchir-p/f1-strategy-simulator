// pybind11 module `f1sim`.
//
// The interface is JSON-shaped on purpose: the backend already has race
// configs and requests as dicts, and the same JSON schema is used by the CLI.
// Python passes JSON text in and gets JSON text back, which keeps the binding
// layer thin and makes the boundary easy to test. The GIL is released while
// the engine runs. Engine errors from bad input surface as ValueError.
//
//   import f1sim, json
//   out = json.loads(f1sim.simulate(json.dumps(race), "VER", "M-18-H", scenarios=2000))

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <string>
#include <stdexcept>

#include "f1sim/config_io.hpp"
#include "f1sim/replay.hpp"
#include "f1sim/strategy.hpp"

#ifndef F1SIM_VERSION
#define F1SIM_VERSION "0.0.0"
#endif

namespace py = pybind11;
using nlohmann::json;
using namespace f1sim;

namespace {

// Laps forced to run under the safety car in every scenario ("what if the
// safety car comes out on lap 30?"). Checked here so bad input is a ValueError.
SimulationOptions make_options(const RaceConfig& race, const std::vector<int>& sc_laps) {
  SimulationOptions o;
  for (int lap : sc_laps) {
    if (lap < 1 || lap > race.total_laps) {
      throw std::invalid_argument("safety car lap " + std::to_string(lap) + " is outside laps 1-" +
                                  std::to_string(race.total_laps));
    }
    o.forced_safety_car_laps.push_back(lap);
  }
  return o;
}

MonteCarloConfig make_config(int scenarios, std::uint64_t seed, int threads,
                             const SimulationOptions& options = {}) {
  MonteCarloConfig c;
  c.scenarios = scenarios;
  c.seed = seed;
  c.threads = threads;
  c.options = options;
  return c;
}

Strategy checked_strategy(const std::string& text, const RaceConfig& race) {
  const Strategy s = parse_strategy(text, race);
  if (auto v = validate_strategy(s, race); !v.ok) throw std::invalid_argument(text + ": " + v.error);
  return s;
}

SearchSpace make_space(int min_stint_laps, int lap_step, int window_start, int window_end,
                       bool one_stop, bool two_stop, int max_candidates,
                       bool respect_stint_limits) {
  SearchSpace space;
  space.min_stint_laps = min_stint_laps;
  space.lap_step = lap_step;
  space.window_start = window_start;
  space.window_end = window_end;
  space.one_stop = one_stop;
  space.two_stop = two_stop;
  space.max_candidates = max_candidates;
  space.respect_stint_limits = respect_stint_limits;
  return space;
}

std::string validate(const std::string& race_json) {
  const RaceConfig race = race_from_json(json::parse(race_json));  // Throws if invalid.
  return json{{"ok", true}, {"drivers", race.drivers.size()}, {"laps", race.total_laps}}.dump();
}

// Returns "" if the strategy is valid for the race, else the reason.
std::string check_strategy(const std::string& race_json, const std::string& strategy_text) {
  const RaceConfig race = race_from_json(json::parse(race_json));
  Strategy s;
  try {
    s = parse_strategy(strategy_text, race);
  } catch (const std::exception& e) {
    return e.what();
  }
  const auto v = validate_strategy(s, race);
  return v.ok ? "" : v.error;
}

// Number of grid candidates the search space would generate (cheap; no
// simulation). `limit` caps generation, so a huge space returns limit + 1.
int count_candidates(const std::string& race_json, int min_stint_laps, int lap_step,
                     int window_start, int window_end, bool one_stop, bool two_stop,
                     bool respect_stint_limits, int limit) {
  const RaceConfig race = race_from_json(json::parse(race_json));
  const SearchSpace space = make_space(min_stint_laps, lap_step, window_start, window_end, one_stop,
                                       two_stop, limit, respect_stint_limits);
  try {
    return static_cast<int>(generate_candidates(race, space).size());
  } catch (const std::invalid_argument&) {
    return limit + 1;
  }
}

std::string simulate(const std::string& race_json, const std::string& driver,
                     const std::string& strategy_text, int scenarios, std::uint64_t seed,
                     int threads, const std::vector<int>& sc_laps,
                     const std::string& compare_with) {
  const RaceConfig race = race_from_json(json::parse(race_json));
  const RaceSimulator sim(race);
  const int d = driver_index_for(driver, race);
  const Strategy s =
      strategy_text.empty() ? race.drivers[d].strategy : parse_strategy(strategy_text, race);
  if (auto v = validate_strategy(s, race); !v.ok) throw std::invalid_argument(v.error);
  const bool comparing = !compare_with.empty();
  const Strategy other = comparing ? checked_strategy(compare_with, race) : s;
  const MonteCarloConfig config = make_config(scenarios, seed, threads, make_options(race, sc_laps));
  MonteCarloResult res;
  MonteCarloResult other_res;
  {
    py::gil_scoped_release release;
    res = run_monte_carlo(sim, d, s, config);
    // Same seed and scenario ids: both strategies see identical races.
    if (comparing) other_res = run_monte_carlo(sim, d, other, config);
  }
  json out = {{"driver", driver},
              {"strategy", strategy_to_json(s, race)},
              {"seed", seed},
              {"scenarios", scenarios},
              {"stats", stats_to_json(res.stats)},
              {"notes", strategy_notes(s, race)}};
  if (comparing) {
    // diff = this strategy - the other, in scored position (positive = worse).
    const auto diff = paired_difference(res.scored_positions, other_res.scored_positions);
    out["comparison"] = {{"strategy", strategy_to_json(other, race)},
                         {"stats", stats_to_json(other_res.stats)},
                         {"notes", strategy_notes(other, race)},
                         {"diff", diff.mean},
                         {"diff_ci_95", {diff.ci_low, diff.ci_high}}};
  }
  return out.dump();
}

// One race, lap by lap. scenario < 0 picks a typical race: one where the
// driver finishes at the median position of `sample` simulated races.
std::string trace(const std::string& race_json, const std::string& driver,
                  const std::string& strategy_text, std::uint64_t seed, long long scenario,
                  int sample, const std::vector<int>& sc_laps) {
  const RaceConfig race = race_from_json(json::parse(race_json));
  const RaceSimulator sim(race);
  const int d = driver_index_for(driver, race);
  const Strategy s = checked_strategy(strategy_text, race);
  const SimulationOptions options = make_options(race, sc_laps);
  if (sample < 1 || sample > 2000) throw std::invalid_argument("sample must be 1-2000");

  json typical = nullptr;
  RaceReplay r;
  {
    py::gil_scoped_release release;
    std::uint64_t k = 0;
    if (scenario < 0) {
      const auto t = typical_scenario(sim, d, s, make_config(sample, seed, 1, options));
      k = t.scenario;
      typical = {{"sample_size", t.sample_size},
                 {"median_position_if_finished", t.median_position_if_finished}};
    } else {
      k = static_cast<std::uint64_t>(scenario);
    }
    r = replay_race(sim, d, s, seed, k, options);
  }

  const auto n = race.drivers.size();
  json drivers = json::array();
  for (std::size_t i = 0; i < n; ++i) {
    const auto& cfg = race.drivers[i];
    json positions = json::array({cfg.grid_position});
    json gaps = json::array({(cfg.grid_position - 1) * race.grid_spacing});
    json pits = json::array();
    int retired_lap = 0;
    for (const auto& lap : r.laps) {
      if (lap.status[i] == DriverStatus::Retired) {
        if (retired_lap == 0) retired_lap = lap.lap;
        positions.push_back(nullptr);
        gaps.push_back(nullptr);
        continue;
      }
      double leader = 1e300;
      for (std::size_t j = 0; j < n; ++j) {
        if (lap.status[j] != DriverStatus::Retired) leader = std::min(leader, lap.cumulative_time[j]);
      }
      positions.push_back(lap.position[i]);
      gaps.push_back(std::round((lap.cumulative_time[i] - leader) * 1000.0) / 1000.0);
      if (lap.pitted[i]) {
        pits.push_back({{"lap", lap.lap},
                        {"compound", strategy_to_json(Strategy{lap.compound_index[i], {}},
                                                      race)["start"]}});
      }
    }
    const auto& dr = r.result.classification[r.result.position_by_driver[i] - 1];
    drivers.push_back({{"code", cfg.code},
                       {"grid", cfg.grid_position},
                       {"strategy", format_strategy(r.strategies[i], race)},
                       {"position", dr.position},
                       {"finished", dr.status != DriverStatus::Retired},
                       {"retired_lap", retired_lap == 0 ? json(nullptr) : json(retired_lap)},
                       {"positions", positions},
                       {"gaps", gaps},
                       {"pits", pits}});
  }
  return json{{"driver", driver},
              {"strategy", format_strategy(s, race)},
              {"seed", seed},
              {"scenario", r.scenario},
              {"typical", typical},
              {"total_laps", race.total_laps},
              {"forced_safety_car_laps", sc_laps},
              {"safety_car_laps", r.result.safety_car_laps},
              {"virtual_safety_car_laps", r.result.virtual_safety_car_laps},
              {"drivers", drivers}}
      .dump();
}

std::string optimize(const std::string& race_json, const std::string& driver, int scenarios,
                     std::uint64_t seed, int threads, int min_stint_laps, int lap_step,
                     int window_start, int window_end, bool one_stop, bool two_stop,
                     int max_candidates, int top_k, bool respect_stint_limits,
                     const std::vector<std::string>& extra_strategies,
                     const std::vector<int>& sc_laps) {
  const RaceConfig race = race_from_json(json::parse(race_json));
  const RaceSimulator sim(race);
  const int d = driver_index_for(driver, race);
  const SearchSpace space = make_space(min_stint_laps, lap_step, window_start, window_end,
                                       one_stop, two_stop, max_candidates, respect_stint_limits);
  auto candidates = generate_candidates(race, space);
  // Extra strategies (e.g. ones the user built) are ranked on the same
  // matched scenarios as the grid.
  std::vector<std::string> extra_labels;
  for (const auto& text : extra_strategies) {
    const Strategy s = parse_strategy(text, race);
    if (auto v = validate_strategy(s, race); !v.ok) {
      throw std::invalid_argument("strategy " + text + ": " + v.error);
    }
    extra_labels.push_back(format_strategy(s, race));
    if (std::find(candidates.begin(), candidates.end(), s) == candidates.end()) {
      candidates.push_back(s);
    }
  }
  OptimizerResult res;
  {
    py::gil_scoped_release release;
    res = evaluate_candidates(sim, d, candidates,
                              make_config(scenarios, seed, threads, make_options(race, sc_laps)));
  }
  json out = optimizer_to_json(res, race, static_cast<std::size_t>(top_k));
  // Always include the extra strategies, even if they fall outside top_k.
  json extras = json::array();
  for (const auto& c : res.ranked) {
    if (std::find(extra_labels.begin(), extra_labels.end(), c.label) != extra_labels.end()) {
      extras.push_back(optimizer_to_json(OptimizerResult{0, 0, {c}}, race, 1)["ranked"][0]);
    }
  }
  out["extra_strategies"] = extras;
  // Every candidate, compactly, for the pit-window chart: [label, mean scored
  // position, finish probability].
  json landscape = json::array();
  for (const auto& c : res.ranked) {
    landscape.push_back({c.label, std::round(c.stats.mean_position * 1000.0) / 1000.0,
                         std::round(c.stats.finish_probability * 1000.0) / 1000.0});
  }
  out["landscape"] = landscape;
  out["driver"] = driver;
  out["seed"] = seed;
  return out.dump();
}

}  // namespace

PYBIND11_MODULE(f1sim, m) {
  m.doc() = "F1 race strategy Monte Carlo engine";
  m.attr("__version__") = F1SIM_VERSION;
  m.def("validate", &validate, py::arg("race_json"));
  m.def("check_strategy", &check_strategy, py::arg("race_json"), py::arg("strategy"));
  m.def("count_candidates", &count_candidates, py::arg("race_json"),
        py::arg("min_stint_laps") = 8, py::arg("lap_step") = 1, py::arg("window_start") = 0,
        py::arg("window_end") = 0, py::arg("one_stop") = true, py::arg("two_stop") = true,
        py::arg("respect_stint_limits") = true, py::arg("limit") = 100000);
  m.def("simulate", &simulate, py::arg("race_json"), py::arg("driver"),
        py::arg("strategy") = "", py::arg("scenarios") = 1000, py::arg("seed") = 42,
        py::arg("threads") = 0, py::arg("sc_laps") = std::vector<int>{},
        py::arg("compare_with") = "");
  m.def("trace", &trace, py::arg("race_json"), py::arg("driver"), py::arg("strategy"),
        py::arg("seed") = 42, py::arg("scenario") = -1, py::arg("sample") = 200,
        py::arg("sc_laps") = std::vector<int>{});
  m.def("optimize", &optimize, py::arg("race_json"), py::arg("driver"),
        py::arg("scenarios") = 500, py::arg("seed") = 42, py::arg("threads") = 0,
        py::arg("min_stint_laps") = 8, py::arg("lap_step") = 1, py::arg("window_start") = 0,
        py::arg("window_end") = 0, py::arg("one_stop") = true, py::arg("two_stop") = true,
        py::arg("max_candidates") = 500, py::arg("top_k") = 10,
        py::arg("respect_stint_limits") = true,
        py::arg("extra_strategies") = std::vector<std::string>{},
        py::arg("sc_laps") = std::vector<int>{});
}

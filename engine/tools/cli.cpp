// Command-line harness for the engine. Runs without the backend or frontend.
//
//   f1sim_cli trace    --config race.json [--driver VER --strategy M-18-H] [--stochastic --seed N --scenario K]
//   f1sim_cli simulate --config race.json --driver VER --strategy M-18-H [--scenarios 2000 --seed 42 --threads 4]
//   f1sim_cli field    --config race.json [--scenarios 2000 --seed 42 --threads 4 --sc-laps 6,7,8 --vsc-laps 20,21]
//   f1sim_cli optimize --config race.json --driver VER [--scenarios 500 --top 10 --min-stint 8
//                      --lap-step 1 --window 10:40 --max-candidates 500 --no-two-stop
//                      --no-stint-limits --also S-11-H-34-H,M-20-H]
//
// All commands print JSON to stdout.

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <map>
#include <algorithm>
#include <sstream>
#include <string>

#include "f1sim/config_io.hpp"
#include "f1sim/strategy.hpp"

using nlohmann::json;
using namespace f1sim;

namespace {

struct Args {
  std::string command;
  std::map<std::string, std::string> values;
  bool has(const std::string& k) const { return values.count(k) > 0; }
  std::string get(const std::string& k, const std::string& def = "") const {
    auto it = values.find(k);
    return it == values.end() ? def : it->second;
  }
  int get_int(const std::string& k, int def) const { return has(k) ? std::stoi(get(k)) : def; }
};

Args parse_args(int argc, char** argv) {
  Args a;
  if (argc < 2) throw std::invalid_argument("missing command (trace|simulate|field|optimize)");
  a.command = argv[1];
  for (int i = 2; i < argc; ++i) {
    std::string k = argv[i];
    if (k.rfind("--", 0) != 0) throw std::invalid_argument("unexpected argument: " + k);
    k = k.substr(2);
    if (i + 1 < argc && std::string(argv[i + 1]).rfind("--", 0) != 0) {
      a.values[k] = argv[++i];
    } else {
      a.values[k] = "true";  // Flag.
    }
  }
  return a;
}

MonteCarloConfig mc_config(const Args& a, int default_scenarios) {
  MonteCarloConfig c;
  c.seed = std::stoull(a.get("seed", "42"));
  c.scenarios = a.get_int("scenarios", default_scenarios);
  c.threads = a.get_int("threads", 0);
  // --sc-laps 6,7,8,9 forces those laps to run under the safety car in every
  // scenario (for replaying a real race's known events).
  if (a.has("sc-laps")) {
    std::stringstream list(a.get("sc-laps"));
    for (std::string item; std::getline(list, item, ',');) {
      if (!item.empty()) c.options.forced_safety_car_laps.push_back(std::stoi(item));
    }
  }
  if (a.has("vsc-laps")) {
    std::stringstream list(a.get("vsc-laps"));
    for (std::string item; std::getline(list, item, ',');) {
      if (!item.empty()) c.options.forced_vsc_laps.push_back(std::stoi(item));
    }
  }
  return c;
}

double seconds_since(std::chrono::steady_clock::time_point t0) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

json cmd_trace(const Args& a, const RaceConfig& race) {
  RaceSimulator sim(race);
  auto strategies = sim.default_strategies();
  if (a.has("driver") && a.has("strategy")) {
    const int d = driver_index_for(a.get("driver"), race);
    strategies[d] = parse_strategy(a.get("strategy"), race);
    if (auto v = validate_strategy(strategies[d], race); !v.ok) throw std::invalid_argument(v.error);
  }
  SimulationOptions opts;
  opts.stochastic = a.has("stochastic");
  std::vector<LapSnapshot> trace;
  const RaceResult r = sim.run(strategies, KeyedRng(std::stoull(a.get("seed", "42"))),
                               std::stoull(a.get("scenario", "0")), opts, &trace);

  json classification = json::array();
  for (const auto& dr : r.classification) {
    classification.push_back({{"position", dr.position},
                              {"driver", race.drivers[dr.driver_index].code},
                              {"status", dr.status == DriverStatus::Retired ? "DNF" : "finished"},
                              {"laps", dr.laps_completed},
                              {"race_time", dr.race_time},
                              {"pit_stops", dr.pit_stops},
                              {"strategy", format_strategy(strategies[dr.driver_index], race)}});
  }
  json laps = json::array();
  for (const auto& s : trace) {
    json order = json::array();
    std::vector<std::string> codes(race.drivers.size());
    for (std::size_t d = 0; d < race.drivers.size(); ++d) codes[s.position[d] - 1] = race.drivers[d].code;
    laps.push_back({{"lap", s.lap}, {"safety_car", s.safety_car}, {"order", codes}});
  }
  return {{"race", race.name},
          {"safety_car_laps", r.safety_car_laps},
          {"virtual_safety_car_laps", r.virtual_safety_car_laps},
          {"classification", classification},
          {"laps", laps}};
}

json cmd_simulate(const Args& a, const RaceConfig& race) {
  RaceSimulator sim(race);
  const int d = driver_index_for(a.get("driver"), race);
  const Strategy s = a.has("strategy") ? parse_strategy(a.get("strategy"), race) : race.drivers[d].strategy;
  if (auto v = validate_strategy(s, race); !v.ok) throw std::invalid_argument(v.error);
  const auto cfg = mc_config(a, 2000);
  const auto t0 = std::chrono::steady_clock::now();
  const auto res = run_monte_carlo(sim, d, s, cfg);
  const double elapsed = seconds_since(t0);
  return {{"driver", race.drivers[d].code},
          {"strategy", strategy_to_json(s, race)},
          {"stats", stats_to_json(res.stats)},
          {"notes", strategy_notes(s, race)},
          {"elapsed_seconds", elapsed},
          {"races_per_second", cfg.scenarios / elapsed}};
}

json cmd_field(const Args& a, const RaceConfig& race) {
  RaceSimulator sim(race);
  const auto cfg = mc_config(a, 2000);
  const auto stats = run_field_monte_carlo(sim, cfg);
  json drivers = json::array();
  for (std::size_t d = 0; d < stats.size(); ++d) {
    drivers.push_back({{"driver", race.drivers[d].code},
                       {"grid", race.drivers[d].grid_position},
                       {"strategy", format_strategy(race.drivers[d].strategy, race)},
                       {"mean_position", stats[d].mean_position},
                       {"p05", stats[d].p05_position},
                       {"p95", stats[d].p95_position},
                       {"p_win", stats[d].p_win},
                       {"dnf_rate", stats[d].dnf_rate},
                       {"finish_probability", stats[d].finish_probability},
                       {"mean_position_if_finished", stats[d].mean_position_if_finished},
                       {"p05_if_finished", stats[d].p05_if_finished},
                       {"p95_if_finished", stats[d].p95_if_finished},
                       {"histogram", stats[d].histogram}});
  }
  return {{"race", race.name}, {"scenarios", cfg.scenarios}, {"drivers", drivers}};
}

json cmd_optimize(const Args& a, const RaceConfig& race) {
  RaceSimulator sim(race);
  const int d = driver_index_for(a.get("driver"), race);
  SearchSpace space;
  space.min_stint_laps = a.get_int("min-stint", space.min_stint_laps);
  space.lap_step = a.get_int("lap-step", space.lap_step);
  space.max_candidates = a.get_int("max-candidates", space.max_candidates);
  space.two_stop = !a.has("no-two-stop");
  space.one_stop = !a.has("no-one-stop");
  space.respect_stint_limits = !a.has("no-stint-limits");
  if (a.has("window")) {
    const std::string w = a.get("window");
    const auto colon = w.find(':');
    if (colon == std::string::npos) throw std::invalid_argument("--window must look like 10:40");
    space.window_start = std::stoi(w.substr(0, colon));
    space.window_end = std::stoi(w.substr(colon + 1));
  }
  const auto cfg = mc_config(a, 500);
  const auto t0 = std::chrono::steady_clock::now();
  // --also adds explicit strategies (comma-separated) to the grid, e.g. the
  // strategy the driver actually ran, so it is ranked on the same scenarios.
  auto candidates = generate_candidates(race, space);
  if (a.has("also")) {
    std::stringstream list(a.get("also"));
    for (std::string item; std::getline(list, item, ',');) {
      const Strategy extra = parse_strategy(item, race);
      if (std::find(candidates.begin(), candidates.end(), extra) == candidates.end()) {
        candidates.push_back(extra);
      }
    }
  }
  const auto res = evaluate_candidates(sim, d, candidates, cfg);
  const double elapsed = seconds_since(t0);
  json out = optimizer_to_json(res, race, static_cast<std::size_t>(a.get_int("top", 10)));
  out["driver"] = race.drivers[d].code;
  out["elapsed_seconds"] = elapsed;
  out["races_simulated"] = static_cast<long long>(res.candidates_evaluated) * cfg.scenarios;
  return out;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Args a = parse_args(argc, argv);
    if (!a.has("config")) throw std::invalid_argument("--config is required");
    const RaceConfig race = load_race(a.get("config"));
    json out;
    if (a.command == "trace") out = cmd_trace(a, race);
    else if (a.command == "simulate") out = cmd_simulate(a, race);
    else if (a.command == "field") out = cmd_field(a, race);
    else if (a.command == "optimize") out = cmd_optimize(a, race);
    else throw std::invalid_argument("unknown command: " + a.command);
    std::cout << out.dump(2) << "\n";
    return EXIT_SUCCESS;
  } catch (const std::exception& e) {
    std::cerr << "error: " << e.what() << "\n";
    return EXIT_FAILURE;
  }
}

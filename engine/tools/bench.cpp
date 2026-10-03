// Fixed, reproducible benchmark workloads.
//
//   f1sim_bench [--config data/examples/2024_italy_pre_race.json] [--driver NOR]
//               [--threads 1,2,4,8] [--repeats 5] [--mc-scenarios 20000]
//               [--opt-scenarios 300] [--json out.json]
//
// Workloads (same race, driver, strategy, seed and candidates every run):
//   single_race  - latency of one stochastic 20-car race, single thread
//   monte_carlo  - N races for one strategy, at each thread count
//   optimizer    - the dashboard's default search (every one- and two-stop
//                  strategy, pit laps every 3 laps) x M races per candidate
//
// Each timed workload is repeated; the median is reported along with the
// min and max. Every workload also reports a checksum of its results, which
// must be identical across thread counts and across engine versions that
// claim not to change behavior: a speedup that changes the answer isn't one.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <thread>

#include "f1sim/config_io.hpp"
#include "f1sim/strategy.hpp"

#ifndef F1SIM_VERSION
#define F1SIM_VERSION "unknown"
#endif
#ifndef F1SIM_CXX_FLAGS
#define F1SIM_CXX_FLAGS "unknown"
#endif

using nlohmann::json;
using namespace f1sim;
using Clock = std::chrono::steady_clock;

namespace {

struct Timing {
  double median = 0, min = 0, max = 0;
};

template <typename F>
Timing time_runs(int repeats, F&& f) {
  std::vector<double> t;
  f();  // Warm-up: page in code and data, start the OpenMP thread pool.
  for (int r = 0; r < repeats; ++r) {
    const auto t0 = Clock::now();
    f();
    t.push_back(std::chrono::duration<double>(Clock::now() - t0).count());
  }
  std::sort(t.begin(), t.end());
  return {t[t.size() / 2], t.front(), t.back()};
}

std::vector<int> parse_list(const std::string& s) {
  std::vector<int> out;
  std::stringstream ss(s);
  for (std::string tok; std::getline(ss, tok, ',');) out.push_back(std::stoi(tok));
  return out;
}

// FNV-1a over integers: a compact fingerprint of a result.
struct Checksum {
  std::uint64_t h = 1469598103934665603ull;
  void add(std::int64_t v) {
    for (int i = 0; i < 8; ++i) {
      h ^= static_cast<std::uint8_t>(v >> (8 * i));
      h *= 1099511628211ull;
    }
  }
  std::string hex() const {
    std::ostringstream o;
    o << std::hex << std::setw(16) << std::setfill('0') << h;
    return o.str();
  }
};

std::string compiler() {
#if defined(__clang__)
  return std::string("clang ") + __clang_version__;
#elif defined(__GNUC__)
  return std::string("gcc ") + __VERSION__;
#else
  return "unknown";
#endif
}

}  // namespace

int main(int argc, char** argv) {
  std::string config = "data/examples/2024_italy_pre_race.json";
  std::string driver = "NOR";
  std::string threads_arg;
  std::string json_out;
  int repeats = 5;
  int mc_scenarios = 20000;
  int opt_scenarios = 300;
  for (int i = 1; i + 1 < argc; i += 2) {
    const std::string k = argv[i];
    const std::string v = argv[i + 1];
    if (k == "--config") config = v;
    else if (k == "--driver") driver = v;
    else if (k == "--threads") threads_arg = v;
    else if (k == "--repeats") repeats = std::stoi(v);
    else if (k == "--mc-scenarios") mc_scenarios = std::stoi(v);
    else if (k == "--opt-scenarios") opt_scenarios = std::stoi(v);
    else if (k == "--json") json_out = v;
    else {
      std::cerr << "unknown option " << k << "\n";
      return 1;
    }
  }

  const unsigned hw = std::max(1u, std::thread::hardware_concurrency());
  std::vector<int> thread_counts;
  if (threads_arg.empty()) {
    for (int t = 1; t <= static_cast<int>(hw); t *= 2) thread_counts.push_back(t);
    if (thread_counts.back() != static_cast<int>(hw)) thread_counts.push_back(static_cast<int>(hw));
  } else {
    thread_counts = parse_list(threads_arg);
  }

  const RaceConfig race = load_race(config);
  const RaceSimulator sim(race);
  const int target = driver_index_for(driver, race);
  const Strategy strategy = race.drivers[target].strategy;
  const std::uint64_t seed = 2024;

  json report;
  report["engine_version"] = F1SIM_VERSION;
  report["compiler"] = compiler();
  report["cxx_flags"] = F1SIM_CXX_FLAGS;
  report["openmp"] = _OPENMP;
  report["hardware_threads"] = hw;
  report["race"] = race.name;
  report["drivers"] = race.drivers.size();
  report["laps"] = race.total_laps;
  report["driver"] = driver;
  report["strategy"] = format_strategy(strategy, race);
  report["seed"] = seed;
  report["repeats"] = repeats;

  std::cout << "f1sim " << F1SIM_VERSION << ", " << compiler() << ", OpenMP " << _OPENMP << "\n"
            << race.name << ": " << race.drivers.size() << " cars x " << race.total_laps
            << " laps, " << driver << " on " << format_strategy(strategy, race) << ", seed " << seed
            << ", median of " << repeats << " runs\n";

  // ---- single race latency ------------------------------------------------------
  {
    const auto strategies = [&] {
      auto s = sim.default_strategies();
      s[target] = strategy;
      return s;
    }();
    const KeyedRng rng(seed);
    const SimulationOptions opts;
    const int runs = 5000;
    Checksum sum;
    const Timing t = time_runs(repeats, [&] {
      Checksum local;
      for (int i = 0; i < runs; ++i) {
        const RaceResult r = sim.run(strategies, rng, static_cast<std::uint64_t>(i), opts);
        local.add(r.position_by_driver[target]);
      }
      sum = local;
    });
    const double us = 1e6 * t.median / runs;
    report["single_race"] = {{"microseconds", us}, {"races", runs}, {"checksum", sum.hex()}};
    std::cout << "\nsingle race: " << std::fixed << std::setprecision(1) << us << " us per race (1 thread)\n";
  }

  // ---- Monte Carlo throughput by thread count -----------------------------------------
  json mc = json::array();
  double mc_base = 0.0;
  std::string mc_checksum;
  bool mc_consistent = true;
  std::cout << "\nmonte carlo: " << mc_scenarios << " races\n"
            << "threads   seconds   races/s   speedup\n";
  for (int t : thread_counts) {
    MonteCarloConfig cfg;
    cfg.seed = seed;
    cfg.scenarios = mc_scenarios;
    cfg.threads = t;
    MonteCarloResult res;
    const Timing tm = time_runs(repeats, [&] { res = run_monte_carlo(sim, target, strategy, cfg); });
    Checksum sum;
    for (int p : res.scored_positions) sum.add(p);
    if (mc_checksum.empty()) mc_checksum = sum.hex();
    mc_consistent = mc_consistent && sum.hex() == mc_checksum;
    if (mc_base == 0.0) mc_base = tm.median;
    mc.push_back({{"threads", t},
                  {"seconds", tm.median},
                  {"seconds_min", tm.min},
                  {"seconds_max", tm.max},
                  {"races_per_second", mc_scenarios / tm.median},
                  {"speedup_vs_1_thread", mc_base / tm.median},
                  {"checksum", sum.hex()}});
    std::cout << std::setw(7) << t << std::setw(10) << std::setprecision(3) << tm.median
              << std::setw(10) << std::setprecision(0) << mc_scenarios / tm.median << std::setw(9)
              << std::setprecision(2) << mc_base / tm.median << "x\n";
  }
  report["monte_carlo"] = {{"races", mc_scenarios},
                           {"results", mc},
                           {"checksum", mc_checksum},
                           {"identical_across_threads", mc_consistent}};

  // ---- optimizer: the dashboard's default search ------------------------------------------
  SearchSpace space;
  space.lap_step = 3;
  space.max_candidates = 3000;
  const auto candidates = generate_candidates(race, space);
  json opt = json::array();
  double opt_base = 0.0;
  std::string opt_checksum;
  bool opt_consistent = true;
  std::cout << "\noptimizer: " << candidates.size() << " strategies x " << opt_scenarios
            << " races = " << candidates.size() * opt_scenarios << " races\n"
            << "threads   seconds   races/s   speedup\n";
  for (int t : thread_counts) {
    MonteCarloConfig cfg;
    cfg.seed = seed;
    cfg.scenarios = opt_scenarios;
    cfg.threads = t;
    OptimizerResult res;
    const Timing tm = time_runs(repeats, [&] { res = evaluate_candidates(sim, target, candidates, cfg); });
    Checksum sum;
    for (const auto& c : res.ranked) {
      for (char ch : c.label) sum.add(ch);
      sum.add(static_cast<std::int64_t>(c.stats.mean_position * 1e6));
    }
    if (opt_checksum.empty()) opt_checksum = sum.hex();
    opt_consistent = opt_consistent && sum.hex() == opt_checksum;
    if (opt_base == 0.0) opt_base = tm.median;
    const double races = static_cast<double>(candidates.size()) * opt_scenarios;
    opt.push_back({{"threads", t},
                   {"seconds", tm.median},
                   {"seconds_min", tm.min},
                   {"seconds_max", tm.max},
                   {"races_per_second", races / tm.median},
                   {"speedup_vs_1_thread", opt_base / tm.median},
                   {"best", res.ranked.front().label},
                   {"checksum", sum.hex()}});
    std::cout << std::setw(7) << t << std::setw(10) << std::setprecision(3) << tm.median
              << std::setw(10) << std::setprecision(0) << races / tm.median << std::setw(9)
              << std::setprecision(2) << opt_base / tm.median << "x   best "
              << res.ranked.front().label << "\n";
  }
  report["optimizer"] = {{"candidates", candidates.size()},
                         {"races_per_candidate", opt_scenarios},
                         {"results", opt},
                         {"checksum", opt_checksum},
                         {"identical_across_threads", opt_consistent}};

  std::cout << "\nchecksums: monte carlo " << mc_checksum << (mc_consistent ? "" : " (DIFFERS by threads!)")
            << ", optimizer " << opt_checksum << (opt_consistent ? "" : " (DIFFERS by threads!)") << "\n";
  if (!json_out.empty()) {
    std::ofstream(json_out) << report.dump(2) << "\n";
    std::cout << "wrote " << json_out << "\n";
  }
  return mc_consistent && opt_consistent ? 0 : 2;
}

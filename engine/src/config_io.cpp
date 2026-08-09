#include "f1sim/config_io.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "f1sim/strategy.hpp"

namespace f1sim {
namespace {

using nlohmann::json;

Designation designation_from(const std::string& raw) {
  std::string s = raw;
  std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
  if (s == "s" || s == "soft") return Designation::Soft;
  if (s == "m" || s == "medium") return Designation::Medium;
  if (s == "h" || s == "hard") return Designation::Hard;
  throw std::invalid_argument("unknown tire designation '" + raw + "'");
}

std::string designation_name(Designation d) {
  switch (d) {
    case Designation::Soft: return "soft";
    case Designation::Medium: return "medium";
    case Designation::Hard: return "hard";
  }
  return "unknown";
}

int absolute_from(const json& j) {
  if (j.is_number_integer()) return j.get<int>();
  std::string s = j.get<std::string>();
  if (!s.empty() && (s[0] == 'C' || s[0] == 'c')) s = s.substr(1);
  return std::stoi(s);
}

}  // namespace

int compound_index_for(Designation d, const RaceConfig& race) {
  for (std::size_t i = 0; i < race.compounds.size(); ++i) {
    if (race.compounds[i].designation == d) return static_cast<int>(i);
  }
  throw std::invalid_argument("compound " + designation_name(d) + " not available at this race");
}

int driver_index_for(const std::string& code, const RaceConfig& race) {
  for (std::size_t i = 0; i < race.drivers.size(); ++i) {
    if (race.drivers[i].code == code) return static_cast<int>(i);
  }
  throw std::invalid_argument("unknown driver '" + code + "'");
}

Strategy strategy_from_json(const json& j, const RaceConfig& race) {
  Strategy s;
  s.start_compound_index =
      compound_index_for(designation_from(j.at("start").get<std::string>()), race);
  for (const auto& stop : j.value("stops", json::array())) {
    s.stops.push_back({stop.at("lap").get<int>(),
                       compound_index_for(designation_from(stop.at("compound").get<std::string>()),
                                          race)});
  }
  return s;
}

json strategy_to_json(const Strategy& s, const RaceConfig& race) {
  json stops = json::array();
  for (const auto& st : s.stops) {
    stops.push_back({{"lap", st.lap},
                     {"compound", std::string(1, designation_letter(
                                                     race.compounds[st.compound_index].designation))}});
  }
  return {{"start", std::string(1, designation_letter(
                                       race.compounds[s.start_compound_index].designation))},
          {"stops", stops},
          {"label", format_strategy(s, race)}};
}

Strategy parse_strategy(const std::string& text, const RaceConfig& race) {
  std::vector<std::string> parts;
  std::stringstream ss(text);
  for (std::string tok; std::getline(ss, tok, '-');) parts.push_back(tok);
  if (parts.empty() || parts.size() % 2 == 0) {
    throw std::invalid_argument("strategy must look like M-18-H or S-12-M-35-H");
  }
  Strategy s;
  s.start_compound_index = compound_index_for(designation_from(parts[0]), race);
  for (std::size_t i = 1; i + 1 < parts.size(); i += 2) {
    s.stops.push_back({std::stoi(parts[i]),
                       compound_index_for(designation_from(parts[i + 1]), race)});
  }
  return s;
}

RaceConfig race_from_json(const json& j) {
  RaceConfig r;
  r.name = j.value("name", std::string("unnamed race"));
  r.season = j.value("season", 2024);
  r.total_laps = j.at("total_laps").get<int>();
  r.fuel_effect_per_lap = j.value("fuel_effect_per_lap", r.fuel_effect_per_lap);
  r.pit_loss_green = j.value("pit_loss_green", r.pit_loss_green);
  r.pit_loss_safety_car = j.value("pit_loss_safety_car", r.pit_loss_safety_car);
  r.pit_loss_virtual_safety_car =
      j.value("pit_loss_virtual_safety_car", r.pit_loss_virtual_safety_car);
  r.grid_spacing = j.value("grid_spacing", r.grid_spacing);
  r.min_compounds_required = j.value("min_compounds_required", r.min_compounds_required);

  for (const auto& c : j.at("compounds")) {
    CompoundParams p;
    p.designation = designation_from(c.at("designation").get<std::string>());
    p.absolute_compound = absolute_from(c.at("absolute_compound"));
    p.pace_offset = c.value("pace_offset", 0.0);
    p.deg_linear = c.value("deg_linear", 0.0);
    p.deg_quadratic = c.value("deg_quadratic", 0.0);
    p.max_stint_laps = c.value("max_stint_laps", 0);
    const std::string conf = c.value("confidence", std::string("race"));
    if (conf == "race") p.confidence = TireConfidence::Race;
    else if (conf == "race_low") p.confidence = TireConfidence::RaceLow;
    else if (conf == "pooled") p.confidence = TireConfidence::Pooled;
    else if (conf == "fallback") p.confidence = TireConfidence::Fallback;
    else throw std::invalid_argument("unknown tire confidence '" + conf + "'");
    r.compounds.push_back(p);
  }

  if (j.contains("safety_car")) {
    const auto& s = j["safety_car"];
    auto& sc = r.safety_car;
    sc.start_probability_per_lap = s.value("start_probability_per_lap", sc.start_probability_per_lap);
    sc.min_duration_laps = s.value("min_duration_laps", sc.min_duration_laps);
    sc.max_duration_laps = s.value("max_duration_laps", sc.max_duration_laps);
    sc.lap_time = s.value("lap_time", sc.lap_time);
    sc.bunched_gap = s.value("bunched_gap", sc.bunched_gap);
    sc.earliest_lap = s.value("earliest_lap", sc.earliest_lap);
    sc.latest_lap_margin = s.value("latest_lap_margin", sc.latest_lap_margin);
  }
  if (j.contains("virtual_safety_car")) {
    const auto& s = j["virtual_safety_car"];
    auto& v = r.virtual_safety_car;
    v.start_probability_per_lap = s.value("start_probability_per_lap", v.start_probability_per_lap);
    v.min_duration_laps = s.value("min_duration_laps", v.min_duration_laps);
    v.max_duration_laps = s.value("max_duration_laps", v.max_duration_laps);
    v.lap_time = s.value("lap_time", v.lap_time);
    v.earliest_lap = s.value("earliest_lap", v.earliest_lap);
    v.latest_lap_margin = s.value("latest_lap_margin", v.latest_lap_margin);
    if (v.start_probability_per_lap < 0.0 || v.start_probability_per_lap > 1.0 ||
        v.min_duration_laps < 1 || v.max_duration_laps < v.min_duration_laps || v.lap_time < 0.0) {
      throw std::invalid_argument("invalid virtual_safety_car parameters");
    }
  }
  if (j.contains("interactions")) {
    const auto& s = j["interactions"];
    auto& in = r.interactions;
    in.min_gap = s.value("min_gap", in.min_gap);
    in.dirty_air_gap = s.value("dirty_air_gap", in.dirty_air_gap);
    in.dirty_air_penalty = s.value("dirty_air_penalty", in.dirty_air_penalty);
    in.overtake_pace_threshold = s.value("overtake_pace_threshold", in.overtake_pace_threshold);
    in.overtake_base_probability = s.value("overtake_base_probability", in.overtake_base_probability);
    in.overtake_probability_per_second =
        s.value("overtake_probability_per_second", in.overtake_probability_per_second);
    in.max_overtake_probability = s.value("max_overtake_probability", in.max_overtake_probability);
  }
  if (j.contains("noise")) {
    r.noise.race_pace_sigma = j["noise"].value("race_pace_sigma", r.noise.race_pace_sigma);
    r.noise.lap_sigma = j["noise"].value("lap_sigma", r.noise.lap_sigma);
  }

  // Drivers last: their strategies refer to the compounds above.
  for (const auto& d : j.at("drivers")) {
    DriverConfig dc;
    dc.code = d.at("code").get<std::string>();
    dc.base_lap_time = d.at("base_lap_time").get<double>();
    dc.grid_position = d.at("grid_position").get<int>();
    dc.dnf_probability = d.value("dnf_probability", 0.0);
    dc.strategy = strategy_from_json(d.at("strategy"), r);
    r.drivers.push_back(dc);
  }

  if (auto v = validate_race(r); !v.ok) throw std::invalid_argument("invalid race config: " + v.error);
  return r;
}

RaceConfig load_race(const std::string& path) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error("cannot open race config: " + path);
  return race_from_json(json::parse(in));
}

json race_to_json(const RaceConfig& r) {
  json compounds = json::array();
  for (const auto& c : r.compounds) {
    compounds.push_back({{"designation", designation_name(c.designation)},
                         {"absolute_compound", "C" + std::to_string(c.absolute_compound)},
                         {"pace_offset", c.pace_offset},
                         {"deg_linear", c.deg_linear},
                         {"deg_quadratic", c.deg_quadratic},
                         {"max_stint_laps", c.max_stint_laps},
                         {"confidence", confidence_name(c.confidence)}});
  }
  json drivers = json::array();
  for (const auto& d : r.drivers) {
    drivers.push_back({{"code", d.code},
                       {"base_lap_time", d.base_lap_time},
                       {"grid_position", d.grid_position},
                       {"dnf_probability", d.dnf_probability},
                       {"strategy", strategy_to_json(d.strategy, r)}});
  }
  const auto& sc = r.safety_car;
  const auto& in = r.interactions;
  return {{"name", r.name},
          {"season", r.season},
          {"total_laps", r.total_laps},
          {"fuel_effect_per_lap", r.fuel_effect_per_lap},
          {"pit_loss_green", r.pit_loss_green},
          {"pit_loss_safety_car", r.pit_loss_safety_car},
          {"pit_loss_virtual_safety_car", r.pit_loss_virtual_safety_car},
          {"grid_spacing", r.grid_spacing},
          {"min_compounds_required", r.min_compounds_required},
          {"compounds", compounds},
          {"safety_car",
           {{"start_probability_per_lap", sc.start_probability_per_lap},
            {"min_duration_laps", sc.min_duration_laps},
            {"max_duration_laps", sc.max_duration_laps},
            {"lap_time", sc.lap_time},
            {"bunched_gap", sc.bunched_gap},
            {"earliest_lap", sc.earliest_lap},
            {"latest_lap_margin", sc.latest_lap_margin}}},
          {"virtual_safety_car",
           {{"start_probability_per_lap", r.virtual_safety_car.start_probability_per_lap},
            {"min_duration_laps", r.virtual_safety_car.min_duration_laps},
            {"max_duration_laps", r.virtual_safety_car.max_duration_laps},
            {"lap_time", r.virtual_safety_car.lap_time},
            {"earliest_lap", r.virtual_safety_car.earliest_lap},
            {"latest_lap_margin", r.virtual_safety_car.latest_lap_margin}}},
          {"interactions",
           {{"min_gap", in.min_gap},
            {"dirty_air_gap", in.dirty_air_gap},
            {"dirty_air_penalty", in.dirty_air_penalty},
            {"overtake_pace_threshold", in.overtake_pace_threshold},
            {"overtake_base_probability", in.overtake_base_probability},
            {"overtake_probability_per_second", in.overtake_probability_per_second},
            {"max_overtake_probability", in.max_overtake_probability}}},
          {"noise", {{"race_pace_sigma", r.noise.race_pace_sigma}, {"lap_sigma", r.noise.lap_sigma}}},
          {"drivers", drivers}};
}

json stats_to_json(const OutcomeStats& s) {
  return {{"scenarios", s.scenarios},
          {"mean_position", s.mean_position},
          {"std_position", s.std_position},
          {"p05_position", s.p05_position},
          {"p50_position", s.p50_position},
          {"p95_position", s.p95_position},
          {"p_win", s.p_win},
          {"p_podium", s.p_podium},
          {"p_points", s.p_points},
          {"dnf_rate", s.dnf_rate},
          {"mean_race_time", s.mean_race_time},
          {"finish_probability", s.finish_probability},
          {"mean_position_if_finished", s.mean_position_if_finished},
          {"p05_if_finished", s.p05_if_finished},
          {"p50_if_finished", s.p50_if_finished},
          {"p95_if_finished", s.p95_if_finished},
          {"histogram", s.histogram}};
}

json optimizer_to_json(const OptimizerResult& r, const RaceConfig& race, std::size_t top_k) {
  json ranked = json::array();
  for (std::size_t i = 0; i < r.ranked.size() && i < top_k; ++i) {
    const auto& c = r.ranked[i];
    ranked.push_back({{"rank", c.rank},
                      {"strategy", strategy_to_json(c.strategy, race)},
                      {"stats", stats_to_json(c.stats)},
                      {"diff_vs_best", c.diff_vs_best},
                      {"diff_ci_95", {c.diff_ci_low, c.diff_ci_high}},
                      {"inconclusive_vs_best", c.inconclusive_vs_best},
                      {"notes", c.notes}});
  }
  return {{"candidates_evaluated", r.candidates_evaluated},
          {"scenarios_per_candidate", r.scenarios_per_candidate},
          {"ranked", ranked}};
}

}  // namespace f1sim

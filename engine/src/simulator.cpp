#include "f1sim/simulator.hpp"

#include <algorithm>
#include <numeric>

namespace f1sim {

ScenarioEvents build_scenario_events(const RaceConfig& race, const KeyedRng& rng,
                                     std::uint64_t scenario, const SimulationOptions& options) {
  const int laps = race.total_laps;
  const int n = static_cast<int>(race.drivers.size());
  ScenarioEvents ev;
  ev.safety_car.assign(laps + 1, 0);
  ev.retirement_lap.assign(n, 0);
  ev.race_pace_offset.assign(n, 0.0);

  if (options.stochastic) {
    const ScenarioStream rs = rng.scenario(scenario);
    // Safety-car timeline: walk the laps, at most one deployment at a time.
    const auto& sc = race.safety_car;
    const int last = laps - sc.latest_lap_margin;
    for (int lap = std::max(1, sc.earliest_lap); lap <= last; ++lap) {
      const LapStream ls = rs.lap(lap);
      if (ls.event(kNoDriver, RandomEvent::SafetyCarStart).uniform() <
          sc.start_probability_per_lap) {
        const int length = ls.event(kNoDriver, RandomEvent::SafetyCarLength)
                               .uniform_int(sc.min_duration_laps, sc.max_duration_laps);
        const int end = std::min(laps, lap + length - 1);
        for (int l = lap; l <= end; ++l) ev.safety_car[l] = 1;
        lap = end + 1;  // At least one green lap before the next deployment.
      }
    }
    const LapStream pre = rs.lap(0);
    for (int d = 0; d < n; ++d) {
      const auto du = static_cast<std::uint32_t>(d);
      ev.race_pace_offset[d] =
          race.noise.race_pace_sigma * pre.event(du, RandomEvent::RacePace).normal();
      if (pre.event(du, RandomEvent::Retirement).uniform() < race.drivers[d].dnf_probability) {
        ev.retirement_lap[d] = pre.event(du, RandomEvent::RetirementLap).uniform_int(1, laps);
      }
    }
  }

  for (int lap : options.forced_safety_car_laps) {
    if (lap >= 1 && lap <= laps) ev.safety_car[lap] = 1;
  }

  // Virtual safety cars, on laps without a safety car (and not straight after
  // one). Drawn only when they can happen, so races without VSCs use exactly
  // the same random numbers as before VSCs existed.
  ev.vsc.assign(laps + 1, 0);
  const auto& vsc = race.virtual_safety_car;
  if (options.stochastic && vsc.start_probability_per_lap > 0.0) {
    const ScenarioStream rs = rng.scenario(scenario);
    const int last = laps - vsc.latest_lap_margin;
    for (int lap = std::max(1, vsc.earliest_lap); lap <= last; ++lap) {
      if (ev.safety_car[lap] || ev.safety_car[lap - 1]) continue;
      const LapStream ls = rs.lap(lap);
      if (ls.event(kNoDriver, RandomEvent::VscStart).uniform() < vsc.start_probability_per_lap) {
        const int length = ls.event(kNoDriver, RandomEvent::VscLength)
                               .uniform_int(vsc.min_duration_laps, vsc.max_duration_laps);
        int l = lap;
        for (; l <= std::min(laps, lap + length - 1) && !ev.safety_car[l]; ++l) ev.vsc[l] = 1;
        lap = l;  // At least one lap gap before the next deployment.
      }
    }
  }
  for (int lap : options.forced_vsc_laps) {
    if (lap >= 1 && lap <= laps && !ev.safety_car[lap]) ev.vsc[lap] = 1;
  }
  for (auto [driver, lap] : options.forced_retirements) {
    if (driver >= 0 && driver < n && lap >= 1 && lap <= laps) ev.retirement_lap[driver] = lap;
  }
  return ev;
}

RaceSimulator::RaceSimulator(const RaceConfig& race)
    : race_(race),
      vsc_lap_time_(race.virtual_safety_car.lap_time > 0.0 ? race.virtual_safety_car.lap_time
                                                           : race.safety_car.lap_time),
      vsc_pit_loss_(race.pit_loss_virtual_safety_car > 0.0
                        ? race.pit_loss_virtual_safety_car
                        : 0.5 * (race.pit_loss_green + race.pit_loss_safety_car)) {}

std::vector<Strategy> RaceSimulator::default_strategies() const {
  std::vector<Strategy> s;
  s.reserve(race_.drivers.size());
  for (const auto& d : race_.drivers) s.push_back(d.strategy);
  return s;
}

double RaceSimulator::clean_lap_time(int driver, int compound_index, int tire_age,
                                     int lap) const {
  const auto& c = race_.compounds[compound_index];
  const double a = tire_age;
  // Fuel load at the start of `lap` is proportional to laps still to run.
  const double fuel_laps = race_.total_laps - lap + 1;
  return race_.drivers[driver].base_lap_time + c.pace_offset + c.deg_linear * a +
         c.deg_quadratic * a * a + race_.fuel_effect_per_lap * fuel_laps;
}

RaceResult RaceSimulator::run(const std::vector<Strategy>& strategies, const KeyedRng& rng,
                              std::uint64_t scenario, const SimulationOptions& options,
                              std::vector<LapSnapshot>* trace) const {
  const ScenarioEvents events = build_scenario_events(race_, rng, scenario, options);
  return run(strategies, rng, scenario, options, events, trace);
}

RaceResult RaceSimulator::run(const std::vector<Strategy>& strategies, const KeyedRng& rng,
                              std::uint64_t scenario, const SimulationOptions& options,
                              const ScenarioEvents& events,
                              std::vector<LapSnapshot>* trace) const {
  const int n = static_cast<int>(race_.drivers.size());
  const int laps = race_.total_laps;
  const auto& inter = race_.interactions;

  // ---- per-driver state ------------------------------------------------------
  std::vector<double> time(n);
  std::vector<double> tentative(n);
  std::vector<double> raw_pace(n);
  std::vector<double> sc_floor(n);  // Fastest possible cumulative time on an SC lap.
  std::vector<int> compound(n);
  std::vector<int> tire_age(n, 0);
  std::vector<std::size_t> next_stop(n, 0);
  std::vector<int> laps_done(n, 0);
  std::vector<int> pit_count(n, 0);
  std::vector<std::uint8_t> pitted(n, 0);
  std::vector<DriverStatus> status(n, DriverStatus::Running);

  for (int d = 0; d < n; ++d) {
    compound[d] = strategies[d].start_compound_index;
    // Grid order is encoded as a small time offset at the start line.
    time[d] = (race_.drivers[d].grid_position - 1) * race_.grid_spacing;
  }

  // Running order at the start of the current lap.
  std::vector<int> order(n);
  std::iota(order.begin(), order.end(), 0);
  std::sort(order.begin(), order.end(),
            [&](int a, int b) { return race_.drivers[a].grid_position < race_.drivers[b].grid_position; });

  RaceResult result;
  if (trace) trace->clear();

  const ScenarioStream rs = rng.scenario(scenario);
  for (int lap = 1; lap <= laps; ++lap) {
    const bool sc = events.safety_car[lap] != 0;
    const bool vsc = !sc && !events.vsc.empty() && events.vsc[lap] != 0;
    const LapStream ls = rs.lap(lap);
    if (sc) result.safety_car_laps.push_back(lap);
    if (vsc) result.virtual_safety_car_laps.push_back(lap);

    // 1. Retirements happen during the lap: the driver does not complete it.
    for (int d = 0; d < n; ++d) {
      if (status[d] == DriverStatus::Running && events.retirement_lap[d] == lap) {
        status[d] = DriverStatus::Retired;
      }
    }

    // 2. Each running driver's own lap (clean air) plus any pit loss.
    for (int d = 0; d < n; ++d) {
      pitted[d] = 0;
      if (status[d] != DriverStatus::Running) continue;
      double lt;
      if (sc) {
        lt = race_.safety_car.lap_time;
        // A car catching the safety-car queue can't lap faster than its own
        // clean racing pace, so bunching closes at most (SC pace - race pace)
        // per lap. This also stops lapped cars from regaining a whole lap.
        sc_floor[d] = time[d] + clean_lap_time(d, compound[d], tire_age[d], lap);
      } else if (vsc) {
        // Everyone holds the same delta time: gaps stay as they are.
        lt = vsc_lap_time_;
      } else {
        lt = clean_lap_time(d, compound[d], tire_age[d], lap) + events.race_pace_offset[d];
        if (options.stochastic) {
          lt += race_.noise.lap_sigma *
                ls.event(static_cast<std::uint32_t>(d), RandomEvent::LapNoise).normal();
        }
      }
      raw_pace[d] = lt;
      double pit_loss = 0.0;
      const auto& stops = strategies[d].stops;
      if (next_stop[d] < stops.size() && stops[next_stop[d]].lap == lap) {
        pitted[d] = 1;
        pit_loss = sc ? race_.pit_loss_safety_car : vsc ? vsc_pit_loss_ : race_.pit_loss_green;
      }
      tentative[d] = time[d] + lt + pit_loss;
      if (sc) sc_floor[d] += pit_loss;
    }

    // 3. Interactions with the car directly ahead in the starting order of
    //    this lap. Cars in the pit lane don't interact with cars on track, and
    //    there is no racing behind the safety car.
    if (!sc && !vsc) {
      int ahead = -1;
      for (int d : order) {
        if (status[d] != DriverStatus::Running) continue;
        if (ahead >= 0 && !pitted[d] && !pitted[ahead]) {
          if (tentative[d] < tentative[ahead] + inter.min_gap) {
            // Caught the car ahead: try to pass, otherwise get held up.
            const double advantage = raw_pace[ahead] - raw_pace[d];
            bool passes = false;
            if (advantage >= inter.overtake_pace_threshold) {
              const double p = std::min(
                  inter.max_overtake_probability,
                  inter.overtake_base_probability +
                      inter.overtake_probability_per_second *
                          (advantage - inter.overtake_pace_threshold));
              passes = options.stochastic
                           ? ls.event(static_cast<std::uint32_t>(d), RandomEvent::Overtake)
                                     .uniform() < p
                           : p >= 0.5;
            }
            if (passes) {
              // The passed car loses a little time in the fight.
              tentative[ahead] = std::max(tentative[ahead], tentative[d] + inter.min_gap);
            } else {
              tentative[d] = tentative[ahead] + inter.min_gap;
            }
          } else if (tentative[d] - tentative[ahead] < inter.dirty_air_gap) {
            tentative[d] += inter.dirty_air_penalty;
          }
        }
        ahead = d;
      }
    }

    // 4. Commit the lap: times, tires, pit stops.
    for (int d = 0; d < n; ++d) {
      if (status[d] != DriverStatus::Running) continue;
      time[d] = tentative[d];
      laps_done[d] = lap;
      if (pitted[d]) {
        compound[d] = strategies[d].stops[next_stop[d]].compound_index;
        tire_age[d] = 0;
        ++next_stop[d];
        ++pit_count[d];
      } else {
        ++tire_age[d];
      }
    }

    // 5. New running order: running cars by time, retired cars behind them
    //    (more laps first, then earlier time).
    //    The comparator is a strict total order (ties broken by index), so any
    //    correct sort gives the same result. Insertion sort is used because
    //    the order changes by only a few swaps per lap: close to O(n) here,
    //    against O(n log n) plus call overhead for std::sort.
    const auto before = [&](int a, int b) {
      const bool ra = status[a] == DriverStatus::Running;
      const bool rb = status[b] == DriverStatus::Running;
      if (ra != rb) return ra;
      if (!ra && laps_done[a] != laps_done[b]) return laps_done[a] > laps_done[b];
      if (time[a] != time[b]) return time[a] < time[b];
      return a < b;  // Deterministic tie-break.
    };
    for (int i = 1; i < n; ++i) {
      const int d = order[i];
      int j = i;
      for (; j > 0 && before(d, order[j - 1]); --j) order[j] = order[j - 1];
      order[j] = d;
    }

    // 6. Behind the safety car the field bunches up.
    if (sc) {
      int prev = -1;
      for (int d : order) {
        if (status[d] != DriverStatus::Running) break;
        if (prev >= 0) {
          const double bunched = std::min(time[d], time[prev] + race_.safety_car.bunched_gap);
          time[d] = std::max(sc_floor[d], bunched);
        }
        prev = d;
      }
    }

    if (trace) {
      LapSnapshot snap;
      snap.lap = lap;
      snap.safety_car = sc;
      snap.virtual_safety_car = vsc;
      snap.cumulative_time = time;
      snap.compound_index = compound;
      snap.tire_age = tire_age;
      snap.status = status;
      snap.pitted = pitted;
      snap.position.assign(n, 0);
      for (int p = 0; p < n; ++p) snap.position[order[p]] = p + 1;
      trace->push_back(std::move(snap));
    }
  }

  // ---- classification ----------------------------------------------------------
  result.position_by_driver.assign(n, 0);
  result.classification.reserve(n);
  for (int p = 0; p < n; ++p) {
    const int d = order[p];
    DriverResult r;
    r.driver_index = d;
    r.position = p + 1;
    r.status = status[d] == DriverStatus::Running ? DriverStatus::Finished : DriverStatus::Retired;
    r.laps_completed = laps_done[d];
    r.race_time = time[d];
    r.pit_stops = pit_count[d];
    result.classification.push_back(r);
    result.position_by_driver[d] = p + 1;
  }
  return result;
}

}  // namespace f1sim

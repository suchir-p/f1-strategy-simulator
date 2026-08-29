# Simulation model

This document describes what the engine models, how, and what it deliberately
leaves out. It is the reference for the "model plausibility" side of
validation; the unit tests cover "engineering correctness".

## Lap time

For driver *d* on lap *l*, on tire compound *c* with age *a* (completed laps on
the set):

```
lap = T_d + Δ_c + α_c·a + β_c·a² + γ·(L − l + 1) + δ_d,s + ε_d,l,s
```

| Term | Meaning | Source |
| --- | --- | --- |
| `T_d` | Driver baseline at this circuit (fresh reference compound, no fuel) | Fitted per driver |
| `Δ_c` | Fresh-tire pace of compound *c* relative to the reference (soft) | Fitted |
| `α_c`, `β_c` | Linear and quadratic degradation per compound | Fitted |
| `γ` | Seconds per lap of fuel remaining (*L* = race laps) | Fitted; **also absorbs track evolution** |
| `δ_d,s` | Persistent "good day / bad day" offset, ~N(0, σ_race²), once per driver per scenario | Default 0.15 s |
| `ε_d,l,s` | Lap-to-lap noise, ~N(0, σ_lap²) | Residual std of the fit |

`γ` and tire age are separable because tire age resets at every pit stop while
fuel load falls steadily, so the fit uses all stints jointly. Track rubbering-in
also falls steadily with lap number, so it cannot be separated from fuel and is
folded into `γ`.

## Pit stops

A stop on lap *l* adds an effective time loss to that lap: `pit_loss_green`
normally, `pit_loss_safety_car` on a safety-car lap and
`pit_loss_virtual_safety_car` on a VSC lap. These are effective losses
relative to cars staying out, not pit-lane transit time. Green stops are
measured as in-lap + out-lap minus two of the driver's normal laps; stops under
a safety car or VSC against the cars on the same two laps that didn't stop.
(Comparing those with normal laps counted the slow neutralized laps as pit
loss: it put the SC pit loss at 63–90 s in Miami, China and Qatar 2024
instead of 12–21 s. Fixed by the same-lap comparison.) Cars in the pit lane
don't interact with cars on track that lap.

## Interactions

Each green lap, every car is compared with the car directly ahead of it in the
order at the start of the lap:

* **Caught the car ahead** (would cross the line less than `min_gap` behind):
  if its raw pace advantage that lap is at least `overtake_pace_threshold`, it
  attempts a pass with probability
  `min(max_p, base_p + per_second · (advantage − threshold))`. A successful
  pass pushes the passed car back to `min_gap` behind; a failed one holds the
  attacker at `min_gap` behind.
* **Close behind but not caught** (within `dirty_air_gap`): loses
  `dirty_air_penalty` that lap.

Only the immediate car ahead is checked, so a car can occasionally gain two
places on one lap without a second check. Lapping and blue flags are not
modeled: every finisher completes every lap.

## Safety cars

The safety-car timeline is drawn per scenario: on each eligible green lap a
deployment starts with probability `start_probability_per_lap` and lasts a
uniformly random 2–5 laps, with at least one green lap between deployments.
On a safety-car lap:

* every car runs `safety_car.lap_time`, there is no racing;
* stops cost `pit_loss_safety_car` instead of `pit_loss_green`;
* gaps are compressed to at most `bunched_gap` behind the car ahead, but a car
  can't gain more than (SC lap time − its clean lap time) on that lap, so
  bunching takes realistic time and lapped cars can't regain a lap.

Strategies are fixed: drivers don't react to a safety car by pitting.

## Virtual safety cars

Drawn the same way on laps without a safety car (and not straight after one),
with their own random keys, so a race with no VSC probability uses exactly the
same random numbers as before VSCs existed. On a VSC lap every car runs
`virtual_safety_car.lap_time`, so gaps are kept exactly (no bunching), nobody
races, and a stop costs `pit_loss_virtual_safety_car`.

## Safety-car rate

Pre-race configs use one safety-car rate learned from the training races; the
engine's VSC is used to replay the real VSC laps in the in-sample check (read
from the leader's lap-by-lap track status; a VSC upgraded to a safety car the
next lap counts as the safety car only).

## Retirements

Each driver retires in a scenario with probability `dnf_probability`, on a
uniformly random lap. Retired drivers are classified behind all finishers
(more laps completed first). For ranking strategies a DNF scores as position
*N* + 1 (21 in a 20-car field). Because retirements are matched across
strategies, they add the same value to every candidate in a scenario and
cancel out of paired comparisons.

## Randomness and matched scenarios

Every random draw is a pure function of
`(seed, scenario, lap, driver, event type, draw index)` (SplitMix64-based
keyed hash; see `engine/src/rng.cpp`). There is no generator state, so:

* two strategies evaluated on the same scenario id see the same safety cars,
  retirements, pace offsets and lap noise;
* a strategy that triggers an extra event (say, an overtake attempt) can't
  shift any other draw;
* results are identical for any OpenMP thread count (tested bit-for-bit).

## Strategy search

Exhaustive grid search over one-stop and two-stop strategies for one driver,
with the other 19 on their default (historical) strategies:

* candidate pit laps are restricted by `min_stint_laps`, an optional pit
  window, and `lap_step`; every candidate must use two distinct dry compounds;
* generation fails loudly above `max_candidates` instead of silently running
  for minutes;
* every candidate is evaluated on the same scenarios, ranked by expected
  scored position, and compared with the top candidate using paired
  differences: a 95% interval `mean ± 1.96·sd/√n`. If the interval includes
  zero the gap is reported as inconclusive at that scenario budget.

## Reporting outcomes

Every outcome is reported two ways: the probability that the driver finishes,
and the finishing-position distribution *given* that they finish
(`finish_probability`, `mean_position_if_finished`, `p05/p50/p95_if_finished`).
Mixing retirements into one distribution makes the top of every interval
"DNF", which looks like good calibration while hiding finishing-position
errors. For ranking strategies, a DNF still scores as position N + 1.

## Calibration constraints

The wear curve is fitted with bounded least squares: `α_c, β_c ≥ 0` (wear
never makes a tire faster), `0 = Δ_soft ≤ Δ_medium ≤ Δ_hard` (harder
compounds never start faster), and `γ ≥ 0`. Unconstrained fits on real 2024
data produced a soft whose wear turned negative after ~15 laps (Hungary) and
a medium that started faster than the soft (Spain). The optimizer exploited
both, picking a 52-lap soft stint and a 58-lap medium stint.

Each compound also stores `max_stint_laps`, the longest tire age in its clean
laps. The optimizer skips candidates whose stints exceed it by more than
`stint_limit_margin` (3 laps), so recommendations stay close to the data the
curve was fitted on. Fixed and user-entered strategies are not restricted.

## Out-of-sample prediction

`data/prediction/predict_races.py` builds a pre-race config for a race from
information available before it starts:

* **Driver pace:** race gap to the field median = `b1·(qualifying gap) +
  b2·(mean race gap over the driver's previous 3 races)`, or `b·(qualifying
  gap)` with no history. Fitted by least squares on training races; the field
  level is the median qualifying lap times a fitted ratio.
* **Tires:** for each Pirelli compound C1–C5, median wear (α, β) and stint
  limit over training races with ≥ 100 clean laps on it, and the median pace
  step between adjacent compounds. The weekend's three nominated compounds are
  assembled from these, so all three are always available.
* **Rates:** retirement rate and safety-car deployments per lap, counted from
  training results; fuel effect and lap noise are training medians.
* **Track constants** taken from the race itself: pit loss, safety-car lap time,
  distance, grid. Teams know these before the race.
* **Race-day variation** σ_race chosen from a grid by leave-one-out log score
  on the training races, where each held-out race is excluded from every
  estimate, including other races' "recent form" features.

Scoring excludes real retirements and uses each driver's simulated
distribution conditional on finishing, so a retirement tail can't inflate
coverage.

## Tire confidence levels

After the constrained fit, each compound gets a confidence level:

* `race`: at least 100 clean laps, and wear after 20 laps between 0.2 s and
  8 s. Qatar's medium and hard had hundreds of laps but fitted zero wear, so
  they fail this check.
* `race_low`: a race fit that fails either test (only used when no pool is
  available, e.g. a single-race calibration).
* `pooled`: median α, β and stint limit for that Pirelli compound over other
  races' `race` fits; its pace offset is placed relative to the nearest
  race-fitted compound using the median step between adjacent compounds.
* `fallback`: the compound has no `race` fit anywhere (all C5 in 2024), so the
  nearest compound's wear is used ×1.25, with stints capped at 15 laps.

Pooled and fallback wear is multiplied by a **track severity** factor: the
median ratio of this race's fitted wear (after 20 laps) to the pooled wear for
the same compounds, clamped to 0.5–3×. Without it, a pooled medium at Bahrain
(severity 2.1) looked nearly wear-free beside the fitted soft and hard, and the
optimizer chased it. Pre-race configs have no race-fitted compounds, so
their severity is 1.

`strategy_notes()` attaches a caveat to any strategy with a stint on a
non-`race` compound or longer than its `max_stint_laps`; the optimizer returns
these with every candidate.

## Robustness of recommendations

`data/robustness/robustness.py` reruns the search on pre-race configs with
tire wear ±20%, no safety cars or about one per race, and pit loss ±2 s, all on
matched scenarios. A recommendation is called robust if in every variant it is
the best candidate or statistically tied with it. This targets parameter
uncertainty, which the Monte Carlo spread (luck within the model) does not
capture.

## Known limitations

* Rivals don't react to the target driver's strategy (no covering an undercut).
* No reactive pit decisions under safety cars or VSCs, no red flags.
* Lap-based, not distance-based: no positions within a lap, no lapping.
* One fitted race per config: parameters don't transfer between circuits.
* Overtaking difficulty is the same at every track, so street circuits
  (Jeddah) are too easy to pass on.
* Dry races only for calibration, so the safety-car rate is lower than for a
  full season, and pre-race predictions don't draw VSCs.
* Every set is treated as new; real drivers sometimes start stints on used sets.
* A compound nobody raced (e.g. the medium in Bahrain 2024) is absent from that config.

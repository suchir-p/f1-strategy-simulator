# Validation and calibration

How the model is calibrated on real 2024 timing data, how well it reproduces and predicts races, and how robust its strategy recommendations are. Summary in the [README](../README.md#validation-and-limitations).

## Calibrating from real races

```bash
pip install -r data/calibration/requirements.txt
python data/calibration/calibrate_season.py            # all 20 dry 2024 races
python data/calibration/calibrate_season.py --refit    # refit from saved snapshots, no download
```

The season script covers every 2024 race except Monaco (lap-1 red flag, no
real strategy), Canada, Britain and Brazil (wet or mixed). Tire nominations
come from Pirelli's pre-race announcements. Each race saves its config, its
real result, a raw-lap snapshot (`*_laps.csv`, `*_session.json`) and its
qualifying best laps, all in `data/calibration/output/`. Single races can be
run with `calibrate_race.py --season 2024 --race Bahrain --compounds C1,C2,C3`.

The tire fit is constrained: wear can never make a tire faster, and a harder
compound can never start faster than a softer one. Without that, the first
real fits let the optimizer "win" Hungary on a 52-lap soft stint. Constraints
alone aren't enough, though, because a constrained fit on little data is
still unreliable, so every compound carries a confidence level:

| Level | When | Parameters | 2024 count |
| --- | --- | --- | --- |
| `race` | ≥ 100 clean laps and plausible wear (0.2–8 s lost after 20 laps) | fitted on this race | 40 |
| `pooled` | thin or implausible race fit, compound seen elsewhere | median of the same Pirelli compound at other races | 10 |
| `fallback` | compound never well-fitted anywhere (every C5) | nearest compound's wear, made 25% harsher, stints capped at 15 laps | 10 |

The season script fits every race once, then refits each with a pool built
from the *other* races. The optimizer won't build stints more than 3 laps
beyond the longest observed one, and every recommended strategy lists notes
when it relies on non-race-fitted tires or a stint longer than the data.

Each driver's strategy is labeled `observed` (382 of 399), `reconstructed`
(17, mostly drivers who retired before a second compound: their observed
stints plus a final stop) or `fallback` (0).

## Validation

### In-sample: can it reproduce a race?

```bash
python data/validation/validate_race.py 2024_bahrain 2024_spain ...
```

Each driver's pace is fitted on the race itself, the field runs its real
strategies 2,000 times with the race's real safety-car laps replayed, and the
mean simulated finishing order is compared with the real one. Real
retirements are excluded. Across all 20 races:

| | Simulator | Starting grid |
| --- | --- | --- |
| Median rank correlation | 0.97 | 0.85 |
| Mean position error | 1.28 places | 2.50 places |

Better than the grid in 19/20 races. The exception is Saudi Arabia (2.47 vs
2.17 places): Norris and Hamilton stayed out under the lap-7 safety car and
lost out in reality, but the model, which uses the same overtaking difficulty
everywhere, lets them recover too easily on a street circuit. Restricting to
drivers with observed strategies changes almost nothing (1.26 places).

### Out-of-sample: can it predict a race?

```bash
python data/prediction/predict_races.py
```

Races are split chronologically: the first 15 are training, the last 5
(United States, Mexico, Las Vegas, Qatar, Abu Dhabi) are held out. A
prediction for a race uses only its starting grid, qualifying laps, tire
nomination, distance and two track constants (pit loss, safety-car lap time),
plus parameters learned from earlier or training races: tire behavior pooled
per Pirelli compound, how qualifying gaps and recent race form translate into
race pace, fuel effect, noise, and safety-car and retirement rates. The
race-day pace variation is chosen by leave-one-out on the training races so
the width of the predicted spread is fitted rather than assumed (empirical
coverage below). Drivers keep the strategies they actually ran, so
this is a strategy-conditional prediction.

| | Model | Starting grid | Qualifying order |
| --- | --- | --- | --- |
| Rank correlation, 5 held-out races | **0.87** | 0.69 | 0.79 |
| Position error, 5 held-out races | **2.02** | 3.03 | – |
| Rank correlation, 15 leave-one-out races | **0.88** | 0.86 | 0.86 |
| Position error, 15 leave-one-out races | **2.00** | 2.33 | – |

The model's log score (mean log probability of each driver's real finishing
position) is −2.26 on the held-out races, against −3.00 for a uniform guess,
and the empirical coverage of the predicted 5th–95th percentile range is 94%
(80 of 85 finishers; nominal 90%), using each driver's distribution given that
they finish. With 85 drivers from 5 races, that is consistent with a
well-calibrated 90% interval but too small a sample to pin it down precisely. Results are identical when restricted to observed strategies.
The gain over the grid is large on the held-out races partly because their
grids were unusually misleading (grid penalties at Austin and Abu Dhabi); the
leave-one-out results are the more representative estimate. (Within leave-one-out, shared estimates for an early
race can use later training races; only the 5-race test is strictly
chronological.) A learned finding:
recent race form predicts race pace much better than qualifying does (fitted
weights 0.69 vs 0.27).

Known biases: the safety-car rate is learned from dry races only (3
deployments in 15 races), so it is lower than for a full season, and pre-race
predictions don't draw virtual safety cars (the in-sample check replays the
real ones).

**Pit loss under a safety car.** Stops under a safety car were once compared
with normal green laps, which counted the slow laps as pit loss (63–90 s
in Miami, China and Qatar instead of 12–21 s). They are now compared with
cars on the same laps. Accuracy was unchanged (within 0.001), since
drivers keep their real strategies in the test; it matters for advice
about stopping under a safety car.

## Are strategy recommendations robust?

```bash
python data/robustness/robustness.py              # pre-race configs
python data/robustness/robustness.py --hindsight  # tires fitted on the race itself
```

Monte Carlo spread covers luck within the model, not whether the model's
parameters are right. This reruns the strategy search with tire wear ±20%, no
safety cars or about one per race, and pit loss ±2 s. A recommendation is
*robust* if it is best or statistically tied with the best in every variant,
and is flagged when it relies on borrowed tire data or a stint longer than
any in the data.

| Race, driver | Actually ran | Pre-race pick | Hindsight pick | Real strategy vs hindsight pick |
| --- | --- | --- | --- | --- |
| Monza, LEC | M-15-H | H-29-M (robust) | M-23-H (robust, flagged) | 0.34 places worse |
| Bahrain, LEC | S-11-H-34-H | M-32-S (sensitive) | S-14-H-35-H (robust) | tied |
| Hungary, LEC | M-23-H-40-M | M-26-S-44-M (robust, flagged) | M-26-H-47-M (robust) | 0.33 places worse |
| Spain, NOR | S-23-M-47-S | M-20-S-44-S (sensitive) | S-17-M-41-M (sensitive) | tied |
| Abu Dhabi, NOR | M-26-H | **M-26-H** (robust) | S-17-S-35-M (sensitive, flagged) | 0.24 places worse |

What this shows:

* **Track-specific tire wear is the decisive input.** With tires fitted on the
  race (hindsight), recommendations use the same compound sequence the teams
  chose in 4 of 5 cases, and Bahrain's real strategy is statistically tied with
  the pick. Pre-race, with only season-pooled tires, Bahrain gets a one-stop,
  because pooled wear is less than half of Bahrain's real wear.
* **Robust is not the same as correct.** ±20% wear doesn't reach a 2× error, so
  Monza's pre-race pick is "robust" but reverses the real compound order.
* **The flags work.** Both picks that lean on C5 softs borrowed from C4, or on
  stints longer than any observed, are flagged.

The hindsight runs also exposed a failure worth knowing about: a pooled
compound at a high-wear track looked nearly wear-free next to the fitted ones,
and the optimizer chased it. Pooled and fallback wear is now scaled by a
*track severity* factor, measured on the race's fitted compounds (2.1× the
season median at Bahrain, 0.6× at Saudi Arabia).

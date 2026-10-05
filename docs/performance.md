# Performance

## Method

`engine/tools/bench.cpp` runs three fixed workloads on the same race, driver,
strategy, seed and candidate list every time:

| Workload | What it measures |
| --- | --- |
| One race | Latency of one stochastic 20-car, 53-lap race (single thread, mean of 5,000) |
| Monte Carlo | 20,000 races of one strategy, at each thread count |
| Search | The dashboard's default search: every one- and two-stop strategy with pit laps every 3 laps (824 candidates) × 300 races |

Config: `data/examples/2024_italy_pre_race.json` (2024 Monza, pre-race
calibration), driver NOR, seed 2024. Each workload runs once to warm up, then
`--repeats` times; the median is reported with min and max.

**Correctness is part of the benchmark.** Every workload reports a checksum
(FNV-1a) of its results: each scenario's finishing position for Monte Carlo,
and every candidate's label and mean position for the search. A change only
counts as an optimization if the checksums are unchanged. The benchmark exits
non-zero if results differ between thread counts.

`scripts/bench.sh` builds Release binaries of the current engine and of the
`perf-baseline` tag (the engine before this work), runs both with the same
benchmark program, and saves a JSON report with the machine description.

Two kinds of speedup are reported separately and should not be multiplied
into one headline number without saying so:

* **Parallel speedup:** time on 1 thread ÷ time on N threads, same engine.
* **Optimization speedup:** baseline engine ÷ optimized engine, both on 1 thread.

## Profile

Callgrind on the fixed workload (1,500 Monte Carlo races plus a 2-race search
pass, single thread), baseline engine:

| Function | Share of instructions |
| --- | --- |
| `KeyedRng::bits` (hashing the random key) | 40.4% |
| `RaceSimulator::run` (the lap loop itself) | 17.1% |
| `cos` (Box-Muller) | 11.9% |
| `KeyedRng::normal` | 6.2% |
| `log` (Box-Muller) | 6.5% |
| `clean_lap_time` | 3.6% |
| `KeyedRng::uniform` | 3.1% |
| `std::sort` of the running order | ~5% |
| Memory allocation | < 1% |

About 64% of the time went into producing random numbers. Allocation barely
registered, so buffer reuse, a common first guess, would not have helped.

## Changes

**1. Hash key prefixes once (−28.7% instructions).** Every draw is a pure
function of (seed, scenario, lap, driver, event, draw), hashed by absorbing
each field with a SplitMix64 mix: 11 mixes per draw, and 22 for each normal
draw. The fields are absorbed in a fixed order, so the partial hash after
(seed, scenario) is the same for every draw in a race, and after (…, lap) for
every draw in a lap. `KeyedRng::scenario()` → `ScenarioStream::lap()` →
`LapStream::event()` → `EventStream` caches these partial hashes, and the mix
of each small field value (lap, driver, event, draw index) is a compile-time
table. A lap-noise normal draw now costs 4 mixes (plus table lookups) instead of 22. The
stateless, order-independent design is unchanged, and so is every value: unit
tests pin outputs computed by the original implementation and check that
streams equal full-key draws.

**2. Insertion sort for the running order (−10.1% instructions).** After
every lap the 20 cars are re-sorted, but the order changes by only a few
swaps. The comparator is a strict total order (ties broken by driver index),
so any correct sort gives the same order; insertion sort is close to linear
on nearly-sorted input and avoids `std::sort`'s overhead.

## Considered and not done

* **Faster normal sampling** (using both Box-Muller outputs, a ziggurat
  sampler, or approximate `log`/`cos`): `log` and `cos` are now about 29% of
  instructions, the largest remaining cost. Any of these changes the random
  numbers and therefore every result, which would require re-running the
  validation. Left for a deliberate model version bump.
* **Reusing scenario events across search candidates:** done later, as part
  of restructuring the search loop (below), where it came for free.
* **Buffer reuse:** allocation is under 1% of instructions.
* **Table of clean lap times:** 3.6%; not worth the extra state.

## Results

MacBook Air (Mac15,12, Apple M3: 4 performance + 4 efficiency cores, 16 GB,
macOS 27.0.1, Apple Clang 21.0.0, libomp, OpenMP 5.1), median of 3, run on
battery:

| Workload | Baseline, 1 thread | Optimized, 1 thread | Optimization speedup |
| --- | --- | --- | --- |
| One race | 53.8 µs | 37.4 µs | 1.44× |
| Monte Carlo, 20,000 races | 1.080 s | 0.746 s | 1.45× |
| Search, 824 × 300 races | 13.34 s | 9.40 s | 1.42× |

Parallel scaling of the optimized engine:

| Threads | Monte Carlo | Speedup | Search | Speedup |
| --- | --- | --- | --- | --- |
| 1 | 0.746 s | 1.00× | 9.40 s | 1.00× |
| 2 | 0.381 s | 1.96× | 4.72 s | 1.99× |
| 4 | 0.201 s | 3.71× | 2.53 s | 3.71× |
| 8 | 0.144 s (138.5k races/s) | 5.17× | 1.96 s | 4.79× |

Near-linear up to the 4 performance cores; the next 4 threads run on
efficiency cores and add less. The optimization speedup is smaller on the M3
(1.42–1.45×) than on the cloud Xeon (1.60–1.67×): the hashing that was
removed is integer multiply work the M3 does comparatively cheaply.

2-vCPU cloud VM (Intel Xeon 2.1 GHz, GCC 13.3, `-O3`, OpenMP 4.5), median of
5 runs, from `results/bench-cloud-2core-xeon.json`:

| Workload | Baseline, 1 thread | Optimized, 1 thread | Optimization speedup | Optimized, 2 threads | Parallel speedup |
| --- | --- | --- | --- | --- | --- |
| One race | 123.6 µs | 77.5 µs | 1.60× | – | – |
| Monte Carlo, 20,000 races | 2.51 s | 1.52 s | 1.65× | 0.75 s | 2.04× |
| Search, 824 × 300 races | 31.8 s | 19.1 s | 1.67× | 9.91 s | 1.93× |

A parallel speedup slightly above 2× on 2 vCPUs is measurement noise on a
shared VM, not real superlinear scaling; repeated runs gave 1.81–2.04×.
Instruction counts (above) are the noise-free measure of the two changes.

Checksums `14f088eeb2f837dc` (Monte Carlo) and `ff787ceb54b1d0e1` (search)
were identical across engines and thread counts, and also across the two
machines (GCC on Linux x86-64, Clang on macOS arm64).

Results from other machines are in `results/` (`scripts/bench.sh` writes
them there). Checksums can differ between platforms, since `log` and `cos`
come from each system's math library, but they must agree across thread
counts and between the baseline and optimized engines on the same machine.

## Search loop restructure

The search originally ran one parallel Monte Carlo batch per candidate: 824
fork/joins, each splitting 300 races across the threads. With T threads each
gets 300 / T races per candidate before waiting for the slowest one, so the
waiting grows with T (37 races per thread at 8 threads; about 5 at 64). Now
one parallel loop covers all 247,200 (candidate, race) pairs, and each race's
safety cars, retirements and race-pace offsets are drawn once and shared by
every candidate. Every pair writes its own slot, so the checksums are
unchanged. On 2 vCPUs: 1 thread 19.1 → 18.6 s (event reuse), 2 threads
1.93× → 1.97×. The difference should grow with core count; rerun
`scripts/bench.sh` to measure it.

## Caveats

* On Apple Silicon, performance and efficiency cores differ in speed, so
  throughput does not scale linearly past the performance-core count.
* Fanless laptops slow down under sustained load. Run plugged in, with
  other apps closed; the min/max columns in the JSON show the spread. (The
  M3 numbers above were measured on battery.)
* The cloud VM's two vCPUs may share a physical core with other tenants,
  so its run-to-run noise is a few percent.

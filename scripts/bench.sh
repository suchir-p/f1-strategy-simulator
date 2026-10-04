#!/usr/bin/env bash
# Benchmarks the engine on this machine and records the hardware it ran on.
#
#   scripts/bench.sh                 # optimized engine vs the pre-profiling baseline
#   scripts/bench.sh --no-baseline   # optimized engine only (about half the time)
#
# Builds a Release copy of the engine in engine/build-bench (and the baseline,
# tag perf-baseline, in a temporary checkout), runs the fixed workloads
# from engine/tools/bench.cpp, and writes results/bench-<machine>-<date>.json.
# Takes about 5-10 minutes on a recent laptop. Plug in and close other apps:
# a fanless laptop slows down under sustained load, which shows up as noise.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BASELINE=0
[[ "${1:-}" == "--no-baseline" ]] && BASELINE=0
REPEATS="${REPEATS:-3}"
mkdir -p results

# ---- machine description ---------------------------------------------------------
if [[ "$(uname)" == "Darwin" ]]; then
  MODEL="$(sysctl -n hw.model)"
  CHIP="$(sysctl -n machdep.cpu.brand_string)"
  PCORES="$(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || sysctl -n hw.physicalcpu)"
  ECORES="$(sysctl -n hw.perflevel1.physicalcpu 2>/dev/null || echo 0)"
  CORES="$(sysctl -n hw.ncpu)"
  MEM_GB="$(( $(sysctl -n hw.memsize) / 1073741824 ))"
  OS="macOS $(sw_vers -productVersion)"
  OMP_ROOT="$(brew --prefix libomp 2>/dev/null || true)"
  LIBOMP="$(brew list --versions libomp 2>/dev/null || echo unknown)"
  POWER="$(pmset -g batt 2>/dev/null | head -1 | sed "s/Now drawing from //; s/'//g" || echo unknown)"
else
  MODEL="$(cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null || echo unknown)"
  CHIP="$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')"
  CORES="$(nproc)"; PCORES="$CORES"; ECORES=0
  MEM_GB="$(( $(grep MemTotal /proc/meminfo | awk '{print $2}') / 1048576 ))"
  OS="$(uname -sr)"; OMP_ROOT=""; LIBOMP="system"; POWER="n/a"
fi

# Thread counts: powers of two up to the performance cores, then all cores.
THREADS="1"
t=2
while (( t <= PCORES )); do THREADS="$THREADS,$t"; t=$((t * 2)); done
(( PCORES & (PCORES - 1) )) && THREADS="$THREADS,$PCORES"
(( CORES > PCORES )) && THREADS="$THREADS,$CORES"

echo "Machine: $MODEL, $CHIP, ${PCORES} performance + ${ECORES} efficiency cores, ${MEM_GB} GB, $OS"
echo "Power:   $POWER"
echo "Threads: $THREADS   Repeats: $REPEATS"
echo

build() {  # build <source dir> <build dir>
  local args=(-S "$1/engine" -B "$2" -DCMAKE_BUILD_TYPE=Release -DF1SIM_BUILD_TESTS=OFF)
  [[ -n "$OMP_ROOT" ]] && args+=(-DOpenMP_ROOT="$OMP_ROOT")
  cmake "${args[@]}" > /dev/null
  cmake --build "$2" -j --target f1sim_bench > /dev/null
}

echo "Building the optimized engine..."
build "$ROOT" "$ROOT/engine/build-bench"
STAMP="$(date +%Y%m%d-%H%M)"
NEW_JSON="results/.bench-new-$STAMP.json"
"$ROOT/engine/build-bench/f1sim_bench" --threads "$THREADS" --repeats "$REPEATS" --json "$NEW_JSON"

BASE_JSON=""
if (( BASELINE )); then
  echo
  echo "Building the baseline (before profiling, tag perf-baseline)..."
  WT="$(mktemp -d)/baseline"
  git worktree add -q --detach "$WT" perf-baseline
  # Same benchmark program, old engine.
  cp "$ROOT/engine/tools/bench.cpp" "$WT/engine/tools/bench.cpp"
  cp "$ROOT/data/examples/2024_italy_pre_race.json" "$WT/data/examples/" 2>/dev/null || true
  # Built inside the temporary checkout, so every run starts from a clean cache.
  build "$WT" "$WT/engine/build"
  BASE_JSON="results/.bench-base-$STAMP.json"
  (cd "$WT" && "$WT/engine/build/f1sim_bench" --threads "$THREADS" \
     --repeats "$REPEATS" --json "$ROOT/$BASE_JSON")
  git worktree remove --force "$WT"
fi

OUT="results/bench-$(echo "$CHIP" | tr -cs 'A-Za-z0-9' '-' | sed 's/-$//')-$STAMP.json"
python3 - "$NEW_JSON" "$BASE_JSON" "$OUT" <<PY
import json, sys
new_path, base_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
new = json.load(open(new_path))
report = {
    "machine": {"model": "$MODEL", "chip": "$CHIP", "performance_cores": int("$PCORES"),
                "efficiency_cores": int("$ECORES"), "logical_cpus": int("$CORES"),
                "memory_gb": int("$MEM_GB"), "os": "$OS", "libomp": "$LIBOMP",
                "power": "$POWER"},
    "optimized": new,
}
if base_path:
    base = json.load(open(base_path))
    report["baseline"] = base
    def t1(r, k): return next(x["seconds"] for x in r[k]["results"] if x["threads"] == 1)
    report["optimization_speedup_1_thread"] = {
        "single_race": base["single_race"]["microseconds"] / new["single_race"]["microseconds"],
        "monte_carlo": t1(base, "monte_carlo") / t1(new, "monte_carlo"),
        "optimizer": t1(base, "optimizer") / t1(new, "optimizer"),
    }
    report["same_results_as_baseline"] = (
        base["monte_carlo"]["checksum"] == new["monte_carlo"]["checksum"]
        and base["optimizer"]["checksum"] == new["optimizer"]["checksum"])
json.dump(report, open(out, "w"), indent=2)

print("\n================ summary ================")
m = report["machine"]
print(f"{m['model']}, {m['chip']}: {m['performance_cores']}P + {m['efficiency_cores']}E cores")
print(f"One race (1 thread): {new['single_race']['microseconds']:.1f} us")
for k, label in (("monte_carlo", f"Monte Carlo, {new['monte_carlo']['races']:,} races"),
                 ("optimizer", f"Search, {new['optimizer']['candidates']} strategies x "
                               f"{new['optimizer']['races_per_candidate']} races")):
    print(f"\n{label}")
    print("threads   seconds   races/s   parallel speedup")
    for r in new[k]["results"]:
        print(f"{r['threads']:7d} {r['seconds']:9.3f} {r['races_per_second']:9.0f} {r['speedup_vs_1_thread']:9.2f}x")
    print("identical results at every thread count:", new[k]["identical_across_threads"])
if base_path:
    s = report["optimization_speedup_1_thread"]
    print(f"\nProfiling-driven speedup vs baseline (1 thread): race {s['single_race']:.2f}x, "
          f"Monte Carlo {s['monte_carlo']:.2f}x, search {s['optimizer']:.2f}x")
    print("Same results as the baseline:", report["same_results_as_baseline"])
print(f"\nSaved {out}")
PY
rm -f "$NEW_JSON" ${BASE_JSON:+"$BASE_JSON"}

// Pure helpers behind the charts: kept out of components so they can be unit tested.

import type { Tire } from "./api";
import { COMPOUND_NAMES, parseStrategy, type Compound } from "./strategy";

// ---- pit window ------------------------------------------------------------------

export interface WindowPoint {
  lap: number;
  mean: number;
  strategy: string;
}

export interface WindowCell {
  lap1: number;
  lap2: number;
  mean: number;
  strategy: string;
}

export interface PitWindow {
  /** One line per tire pair ("M>H"), points sorted by pit lap. */
  oneStop: { pair: string; label: string; points: WindowPoint[] }[];
  /** Best strategy for each (first stop, second stop) pair, over tire choices. */
  twoStop: WindowCell[];
  best: { strategy: string; mean: number } | null;
}

export function pitWindow(landscape: [string, number, number][]): PitWindow {
  const pairs = new Map<string, WindowPoint[]>();
  const cells = new Map<string, WindowCell>();
  let best: PitWindow["best"] = null;
  for (const [strategy, mean] of landscape) {
    const s = parseStrategy(strategy);
    if (!s) continue;
    if (!best || mean < best.mean) best = { strategy, mean };
    if (s.stops.length === 1) {
      const pair = `${s.start}>${s.stops[0].compound}`;
      if (!pairs.has(pair)) pairs.set(pair, []);
      pairs.get(pair)!.push({ lap: s.stops[0].lap, mean, strategy });
    } else if (s.stops.length === 2) {
      const key = `${s.stops[0].lap}:${s.stops[1].lap}`;
      const prev = cells.get(key);
      if (!prev || mean < prev.mean) {
        cells.set(key, { lap1: s.stops[0].lap, lap2: s.stops[1].lap, mean, strategy });
      }
    }
  }
  const order = ["S", "M", "H"];
  const oneStop = [...pairs.entries()]
    .sort(([a], [b]) => order.indexOf(a[0]) - order.indexOf(b[0]) || order.indexOf(a[2]) - order.indexOf(b[2]))
    .map(([pair, points]) => ({
      pair,
      label: `${COMPOUND_NAMES[pair[0] as Compound]} → ${COMPOUND_NAMES[pair[2] as Compound].toLowerCase()}`,
      points: points.sort((a, b) => a.lap - b.lap),
    }));
  return { oneStop, twoStop: [...cells.values()], best };
}

/** Typical half-width of the paired 95% intervals: gaps smaller than this are noise. */
export function noiseHalfWidth(cis: [number, number][]): number | null {
  const widths = cis.map(([lo, hi]) => (hi - lo) / 2).filter((w) => w > 0).sort((a, b) => a - b);
  if (!widths.length) return null;
  return widths[Math.floor(widths.length / 2)];
}

// ---- tires -----------------------------------------------------------------------

/** Tire-only lap-time effect at a given tire age (seconds), as the engine models it. */
export function tireEffect(t: Pick<Tire, "pace_offset" | "deg_linear" | "deg_quadratic">, age: number) {
  return t.pace_offset + t.deg_linear * age + t.deg_quadratic * age * age;
}

// ---- safety-car what-if ---------------------------------------------------------

/** "30-32" -> [30, 31, 32]; "" or anything malformed -> []. */
export function lapsFromParam(text: string | null): number[] {
  const m = text?.match(/^(\d{1,2})-(\d{1,2})$/);
  if (!m) return [];
  const a = Number(m[1]);
  const b = Number(m[2]);
  if (a < 1 || b < a || b - a > 9) return [];
  return Array.from({ length: b - a + 1 }, (_, i) => a + i);
}

export function lapsToParam(laps: number[]): string | null {
  return laps.length ? `${laps[0]}-${laps[laps.length - 1]}` : null;
}

/** Contiguous runs, e.g. [3,4,5,9] -> [[3,5],[9,9]]. */
export function lapRuns(laps: number[]): [number, number][] {
  const out: [number, number][] = [];
  for (const lap of [...laps].sort((a, b) => a - b)) {
    const last = out[out.length - 1];
    if (last && lap === last[1] + 1) last[1] = lap;
    else out.push([lap, lap]);
  }
  return out;
}

export function formatLapRuns(laps: number[]): string {
  return lapRuns(laps)
    .map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`))
    .join(", ");
}

// ---- paired comparison ----------------------------------------------------------

/** "Compared with X: 0.10 places worse on average (...)" for the outcome panel. */
export function comparedSentence(against: string, diff: number, ci: [number, number]): string {
  const range = `95% interval ${signed(ci[0])} to ${signed(ci[1])}`;
  const size = Math.abs(diff).toFixed(2);
  if (ci[1] < 0) return `Compared with ${against}: ${size} places better on average (${range}).`;
  if (ci[0] > 0) return `Compared with ${against}: ${size} places worse on average (${range}).`;
  return `Compared with ${against}: no clear difference (${signed(diff)} places, ${range}).`;
}

export const signed = (x: number, digits = 2) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(digits)}`;

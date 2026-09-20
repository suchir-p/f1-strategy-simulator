// Strategy model shared by the editor, the API calls and the result views.
// Text form matches the backend and engine: "M-24-H" = start on mediums,
// pit at the end of lap 24 for hards.

export type Compound = "S" | "M" | "H";

export interface Stop {
  lap: number;
  compound: Compound;
}

export interface Strategy {
  start: Compound;
  stops: Stop[];
}

export interface Stint {
  compound: Compound;
  startLap: number; // First lap on this set.
  endLap: number; // Last lap on this set, inclusive.
}

export const COMPOUND_NAMES: Record<Compound, string> = {
  S: "Soft",
  M: "Medium",
  H: "Hard",
};

export const MIN_STINT = 3;

export function formatStrategy(s: Strategy): string {
  return s.start + s.stops.map((x) => `-${x.lap}-${x.compound}`).join("");
}

const PATTERN = /^[SMH](-\d{1,2}-[SMH]){0,3}$/;

export function parseStrategy(text: string): Strategy | null {
  const t = text.trim().toUpperCase();
  if (!PATTERN.test(t)) return null;
  const parts = t.split("-");
  const stops: Stop[] = [];
  for (let i = 1; i < parts.length; i += 2) {
    stops.push({ lap: Number(parts[i]), compound: parts[i + 1] as Compound });
  }
  return { start: parts[0] as Compound, stops };
}

export function toStints(s: Strategy, totalLaps: number): Stint[] {
  const stints: Stint[] = [];
  let start = 1;
  let compound = s.start;
  for (const stop of s.stops) {
    stints.push({ compound, startLap: start, endLap: stop.lap });
    start = stop.lap + 1;
    compound = stop.compound;
  }
  stints.push({ compound, startLap: start, endLap: totalLaps });
  return stints;
}

export function stintLength(st: Stint): number {
  return st.endLap - st.startLap + 1;
}

/** Returns a reason the engine would reject the strategy, or null if valid. */
export function validateStrategy(
  s: Strategy,
  totalLaps: number,
  available: Compound[],
): string | null {
  const used = [s.start, ...s.stops.map((x) => x.compound)];
  const missing = used.find((c) => !available.includes(c));
  if (missing) return `${COMPOUND_NAMES[missing]} tires aren't available at this race.`;
  let prev = 0;
  for (const stop of s.stops) {
    if (stop.lap <= prev) return "Pit stops must be in lap order.";
    if (stop.lap >= totalLaps) return `The last stop must be before lap ${totalLaps}.`;
    prev = stop.lap;
  }
  if (toStints(s, totalLaps).some((st) => stintLength(st) < 1)) return "Every stint needs at least one lap.";
  if (new Set(used).size < 2) return "Use at least two different compounds, as the rules require.";
  return null;
}

/** Moves stop `index` to `lap`, keeping every stint at least MIN_STINT laps. */
export function moveStop(s: Strategy, index: number, lap: number, totalLaps: number): Strategy {
  const lo = (index === 0 ? 0 : s.stops[index - 1].lap) + MIN_STINT;
  const hi = (index === s.stops.length - 1 ? totalLaps : s.stops[index + 1].lap) - MIN_STINT;
  const clamped = Math.max(lo, Math.min(hi, Math.round(lap)));
  return { ...s, stops: s.stops.map((x, i) => (i === index ? { ...x, lap: clamped } : x)) };
}

/** Sets the compound of stint `index` (0 = the starting set). */
export function setStintCompound(s: Strategy, index: number, compound: Compound): Strategy {
  if (index === 0) return { ...s, start: compound };
  return { ...s, stops: s.stops.map((x, i) => (i === index - 1 ? { ...x, compound } : x)) };
}

/** Adds a stop in the middle of the longest stint, on a different compound. */
export function addStop(s: Strategy, totalLaps: number, available: Compound[]): Strategy {
  if (s.stops.length >= 3) return s;
  const stints = toStints(s, totalLaps);
  let longest = 0;
  stints.forEach((st, i) => {
    if (stintLength(st) > stintLength(stints[longest])) longest = i;
  });
  const st = stints[longest];
  if (stintLength(st) < 2 * MIN_STINT) return s;
  const lap = Math.floor((st.startLap + st.endLap) / 2);
  const compound = available.find((c) => c !== st.compound) ?? st.compound;
  // The new stint takes the new compound; the stint after it keeps its own.
  const stops = [...s.stops];
  stops.splice(longest, 0, { lap, compound });
  return { ...s, stops };
}

export function removeStop(s: Strategy, index: number): Strategy {
  return { ...s, stops: s.stops.filter((_, i) => i !== index) };
}

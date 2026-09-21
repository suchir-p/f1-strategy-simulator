// Small shared pieces.

import type { Confidence, Tire } from "@/lib/api";
import { toStints, stintLength, type Compound, parseStrategy } from "@/lib/strategy";

export function WarnIcon() {
  return (
    <svg className="warn-icon" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 1.5 15 14H1L8 1.5Z" fill="var(--warn)" />
      <path d="M8 6v3.6" stroke="#14181e" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="8" cy="11.6" r="0.95" fill="#14181e" />
    </svg>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export const CONFIDENCE_TEXT: Record<Confidence, string> = {
  race: "fitted on this race",
  race_low: "fitted on few laps",
  pooled: "estimated from other races",
  fallback: "borrowed from a neighboring compound",
};

const DESIGNATION_LETTER: Record<Tire["designation"], Compound> = { soft: "S", medium: "M", hard: "H" };

/** Compound and Pirelli C-number. Data-quality details live in the caveat
 * box and the tire-wear chart, so they aren't repeated here. */
export function TireChip({ tire }: { tire: Tire }) {
  return (
    <span className="tire">
      <span className="tire-dot" data-compound={DESIGNATION_LETTER[tire.designation]} aria-hidden="true" />
      <span>
        {tire.designation[0].toUpperCase() + tire.designation.slice(1)} {tire.absolute_compound}
      </span>
    </span>
  );
}

export function MiniStints({ code, totalLaps }: { code: string; totalLaps: number }) {
  const s = parseStrategy(code);
  if (!s) return null;
  return (
    <span className="mini-stints" aria-hidden="true">
      {toStints(s, totalLaps).map((st, i) => (
        <span key={i} data-compound={st.compound} style={{ flex: stintLength(st) }} />
      ))}
    </span>
  );
}

export const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

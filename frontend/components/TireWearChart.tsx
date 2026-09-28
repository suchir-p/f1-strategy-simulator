"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { Tire } from "@/lib/api";
import { tireEffect } from "@/lib/analysis";
import { COMPOUND_NAMES, stintLength, toStints, type Compound, type Strategy } from "@/lib/strategy";
import { CONFIDENCE_TEXT } from "./bits";

interface Props {
  tires: Record<Compound, Tire | undefined>;
  strategy: Strategy | null;
  totalLaps: number;
}

const LINE: Record<Compound, string> = { S: "var(--tire-s)", M: "var(--tire-m-line)", H: "var(--tire-h-line)" };
const ORDER: Compound[] = ["S", "M", "H"];

function Tip({ active, payload, label }: Partial<TooltipContentProps<number, string>>) {
  if (!active || !payload?.length) return null;
  const seen = new Set<string>();
  const rows = payload.filter((p) => {
    const c = String(p.dataKey)[0];
    if (typeof p.value !== "number" || seen.has(c)) return false;
    seen.add(c);
    return true;
  });
  return (
    <div className="tooltip">
      <strong>Tire age {label} laps</strong>
      {rows.map((p) => {
        const c = String(p.dataKey)[0] as Compound;
        return (
          <span key={c} className="tooltip-row">
            <i style={{ background: LINE[c] }} aria-hidden="true" />
            {COMPOUND_NAMES[c]}: +{Number(p.value).toFixed(2)} s
            {String(p.dataKey).endsWith("ext") ? " (extrapolated)" : ""}
          </span>
        );
      })}
    </div>
  );
}

/**
 * Lap-time cost of each tire by age, exactly as the simulator models it:
 * pace offset + linear + quadratic wear. Solid where the curve was fitted on
 * real stints; dashed beyond the longest stint in the data.
 */
export function TireWearChart({ tires, strategy, totalLaps }: Props) {
  const present = ORDER.filter((c) => tires[c]);
  const reference = Math.min(...present.map((c) => tires[c]!.pace_offset));
  const fastest = present.find((c) => tires[c]!.pace_offset === reference);
  const longest = Math.max(...present.map((c) => tires[c]!.max_stint_laps));
  const stints = strategy ? toStints(strategy, totalLaps) : [];
  const yourLongest = Math.max(0, ...stints.map(stintLength));
  const maxAge = Math.min(totalLaps, Math.max(longest + 10, yourLongest + 3));

  const rows = useMemo(() => {
    const out: Record<string, number | null>[] = [];
    for (let age = 0; age <= maxAge; age++) {
      const row: Record<string, number | null> = { age };
      for (const c of present) {
        const t = tires[c]!;
        const v = +(tireEffect(t, age) - reference).toFixed(3);
        const limit = t.max_stint_laps || maxAge;
        row[`${c}obs`] = age <= limit ? v : null;
        row[`${c}ext`] = age >= limit ? v : null;
      }
      out.push(row);
    }
    return out;
  }, [present, tires, reference, maxAge]);

  if (!present.length || fastest === undefined) return null;
  const peak = Math.max(...rows.flatMap((r) => Object.entries(r).filter(([k]) => k !== "age").map(([, v]) => v ?? 0)));
  const yStep = peak > 6 ? 2 : 1;
  const yTicks = Array.from({ length: Math.ceil(peak / yStep) + 1 }, (_, i) => i * yStep);

  return (
    <figure className="tire-wear">
      <ul className="legend" aria-label="Compounds">
        {present.map((c) => (
          <li key={c}>
            <i style={{ background: LINE[c] }} aria-hidden="true" />
            <span>
              {COMPOUND_NAMES[c]} {tires[c]!.absolute_compound}{" "}
              <small>{CONFIDENCE_TEXT[tires[c]!.confidence]}</small>
            </span>
          </li>
        ))}
        <li>
          <i className="legend-dash" aria-hidden="true" />
          <small>Beyond the longest stint in the data</small>
        </li>
        {stints.length > 0 && (
          <li>
            <i className="legend-yours" aria-hidden="true" />
            <small>End of each of your stints</small>
          </li>
        )}
      </ul>
      <div style={{ width: "100%", height: 230 }}>
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 12, left: -6, bottom: 8 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis
              dataKey="age"
              type="number"
              domain={[0, maxAge]}
              tickLine={false}
              axisLine={{ stroke: "var(--rule-strong)" }}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              label={{ value: "Tire age (laps)", position: "insideBottomRight", offset: -2, fill: "var(--muted)", fontSize: 12 }}
              height={34}
            />
            <YAxis
              domain={[0, yTicks[yTicks.length - 1]]}
              ticks={yTicks}
              tickFormatter={(v: number) => `+${v}s`}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              width={52}
            />
            <Tooltip content={<Tip />} isAnimationActive={false} />
            {present.map((c) => [
              <Line
                key={`${c}obs`}
                dataKey={`${c}obs`}
                stroke={LINE[c]}
                strokeWidth={2.5}
                dot={false}
                isAnimationActive={false}
              />,
              <Line
                key={`${c}ext`}
                dataKey={`${c}ext`}
                stroke={LINE[c]}
                strokeWidth={2}
                strokeDasharray="4 4"
                dot={false}
                isAnimationActive={false}
                activeDot={false}
              />,
            ])}
            {stints.map((st, i) => {
              const t = tires[st.compound];
              if (!t) return null;
              const len = stintLength(st);
              return (
                <ReferenceDot
                  key={i}
                  x={len}
                  y={+(tireEffect(t, len) - reference).toFixed(3)}
                  r={5}
                  fill={LINE[st.compound]}
                  stroke="var(--ink)"
                  strokeWidth={1.5}
                  ifOverflow="extendDomain"
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="chart-sub">
        Seconds per lap slower than a fresh {COMPOUND_NAMES[fastest].toLowerCase()}, from the tires alone (fuel burn
        is modeled separately).{" "}
        {present
          .map((c) => {
            const t = tires[c]!;
            return `${COMPOUND_NAMES[c]}: +${(tireEffect(t, 20) - tireEffect(t, 0)).toFixed(1)} s after 20 laps`;
          })
          .join(", ")}
        .
      </figcaption>
    </figure>
  );
}

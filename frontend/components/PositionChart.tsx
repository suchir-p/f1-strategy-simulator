"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";

interface Props {
  distribution: number[]; // Index 0 = P1, conditional on finishing.
  actualPosition?: number | null;
  /** Labels for the legend and tooltip when a second series is shown. */
  label?: string;
  compare?: { label: string; distribution: number[] } | null;
}

type Row = { pos: string; p: number; q?: number };

function Tip({
  active,
  payload,
  label,
  compareLabel,
}: Partial<TooltipContentProps<number, string>> & { label?: string; compareLabel?: string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as Row;
  if (compareLabel === undefined)
    return (
      <div className="tooltip">
        <strong>{(row.p * 100).toFixed(1)}%</strong>
        <span>finish {row.pos}</span>
      </div>
    );
  return (
    <div className="tooltip">
      <strong>Finish {row.pos}</strong>
      <span className="tooltip-row">
        <i style={{ background: "var(--data)" }} aria-hidden="true" />
        {label}: {(row.p * 100).toFixed(1)}%
      </span>
      <span className="tooltip-row">
        <i style={{ background: "var(--data-2)" }} aria-hidden="true" />
        {compareLabel}: {((row.q ?? 0) * 100).toFixed(1)}%
      </span>
    </div>
  );
}

/** Finishing-position distribution, given the driver finishes; optionally
 * side by side with a second strategy's distribution. */
export function PositionChart({ distribution, actualPosition, label, compare }: Props) {
  const data: Row[] = distribution.map((p, i) => ({ pos: `P${i + 1}`, p, q: compare?.distribution[i] }));
  // Even, round ticks: the smallest "nice" step that needs at most 5 ticks.
  const max = Math.max(...distribution, ...(compare?.distribution ?? []), 0.05);
  const step = [0.05, 0.1, 0.2, 0.25].find((s) => Math.ceil(max / s) <= 4) ?? 0.25;
  const top = Math.min(1, Math.ceil(max / step) * step);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => +(i * step).toFixed(2));
  return (
    <figure style={{ margin: 0 }}>
      {compare && (
        <ul className="legend">
          <li>
            <i style={{ background: "var(--data)" }} aria-hidden="true" />
            {label}
          </li>
          <li>
            <i style={{ background: "var(--data-2)" }} aria-hidden="true" />
            {compare.label}
          </li>
        </ul>
      )}
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <BarChart
            data={data}
            margin={{ top: 30, right: 4, left: -12, bottom: 0 }}
            barCategoryGap={compare ? "12%" : 2}
            barGap={1}
          >
            <CartesianGrid vertical={false} stroke="var(--rule)" />
            <XAxis
              dataKey="pos"
              tickLine={false}
              axisLine={{ stroke: "var(--rule-strong)" }}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              interval="preserveStartEnd"
              minTickGap={6}
            />
            <YAxis
              domain={[0, top]}
              ticks={ticks}
              tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              width={44}
            />
            <Tooltip
              content={<Tip label={label} compareLabel={compare?.label} />}
              cursor={{ fill: "var(--wash)" }}
              isAnimationActive={false}
            />
            {actualPosition ? (
              <ReferenceLine
                x={`P${actualPosition}`}
                stroke="var(--ink)"
                strokeWidth={1.5}
                label={{
                  value: `Actual P${actualPosition}`,
                  position: "top",
                  offset: 14,
                  fill: "var(--ink)",
                  fontSize: 12,
                }}
              />
            ) : null}
            <Bar
              dataKey="p"
              fill="var(--data)"
              radius={[4, 4, 0, 0]}
              maxBarSize={24}
              isAnimationActive={false}
              activeBar={{ fill: "var(--data-hover)" }}
            />
            {compare && (
              <Bar dataKey="q" fill="var(--data-2)" radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="visually-hidden">
        {compare ? `${label}: ` : ""}Probability of each finishing position if the driver finishes:{" "}
        {data
          .filter((d) => d.p >= 0.005)
          .map((d) => `${d.pos} ${(d.p * 100).toFixed(1)}%`)
          .join(", ")}
        {compare &&
          `. ${compare.label}: ${compare.distribution
            .map((q, i) => ({ q, i }))
            .filter((d) => d.q >= 0.005)
            .map((d) => `P${d.i + 1} ${(d.q * 100).toFixed(1)}%`)
            .join(", ")}`}
      </figcaption>
    </figure>
  );
}

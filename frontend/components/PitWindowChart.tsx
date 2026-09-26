"use client";

import { useMemo, useState } from "react";
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
import type { OptimizationResult } from "@/lib/api";
import { noiseHalfWidth, pitWindow, type WindowCell } from "@/lib/analysis";

interface Props {
  result: OptimizationResult;
  totalLaps: number;
  yourStrategy: string | null;
  onUse: (code: string) => void;
}

const LINE_COLORS = ["var(--cat-1)", "var(--cat-2)", "var(--cat-3)", "var(--cat-4)", "var(--cat-5)", "var(--cat-6)"];

type Row = { lap: number } & Record<string, number | string | undefined>;

function OneStopTip({ active, payload, label }: Partial<TooltipContentProps<number, string>>) {
  if (!active || !payload?.length) return null;
  const rows = [...payload].filter((p) => typeof p.value === "number").sort((a, b) => Number(a.value) - Number(b.value));
  return (
    <div className="tooltip">
      <strong>Pit on lap {label}</strong>
      {rows.map((p) => (
        <span key={String(p.dataKey)} className="tooltip-row">
          <i style={{ background: p.color }} aria-hidden="true" />
          {(p.payload as Row)[`${String(p.dataKey)}:code`]}: avg. P{Number(p.value).toFixed(2)}
        </span>
      ))}
    </div>
  );
}

/**
 * The shape of the search: how the average finishing position changes with
 * the pit lap, per tire pair (one stop), or with both stop laps (two stops).
 * A flat curve means a forgiving window; a sharp dip means timing matters.
 */
export function PitWindowChart({ result, totalLaps, yourStrategy, onUse }: Props) {
  const w = useMemo(() => pitWindow(result.landscape), [result.landscape]);
  const hasOne = w.oneStop.length > 0;
  const hasTwo = w.twoStop.length > 0;
  const bestIsTwo = !!w.best && w.best.strategy.split("-").length === 5;
  const [tab, setTab] = useState<"one" | "two">(bestIsTwo && hasTwo ? "two" : "one");
  const view = tab === "two" && hasTwo ? "two" : hasOne ? "one" : "two";
  const noise = noiseHalfWidth(result.ranked.slice(1).map((c) => c.comparison_vs_best.ci95));

  if (!result.landscape.length) return null;

  return (
    <div className="pit-window">
      <div className="pit-window-head">
        <h3 className="chart-title">Pit window</h3>
        {hasOne && hasTwo && (
          <div className="segmented segmented-sm" role="group" aria-label="Number of stops">
            <button type="button" aria-pressed={view === "one"} onClick={() => setTab("one")}>
              One stop
            </button>
            <button type="button" aria-pressed={view === "two"} onClick={() => setTab("two")}>
              Two stops
            </button>
          </div>
        )}
      </div>
      <p className="chart-sub">
        {view === "one"
          ? "Average finish by pit lap for each tire choice; higher is better."
          : "Best average finish for each pair of stop laps; darker is better."}{" "}
        Select a point to load it.
        {noise !== null && ` Differences under ${noise.toFixed(2)} places are noise.`}
      </p>
      {view === "one" ? (
        <OneStop w={w} totalLaps={totalLaps} yourStrategy={yourStrategy} onUse={onUse} />
      ) : (
        <TwoStop cells={w.twoStop} best={w.best?.strategy ?? null} yourStrategy={yourStrategy} onUse={onUse} />
      )}
    </div>
  );
}

function OneStop({
  w,
  totalLaps,
  yourStrategy,
  onUse,
}: {
  w: ReturnType<typeof pitWindow>;
  totalLaps: number;
  yourStrategy: string | null;
  onUse: (code: string) => void;
}) {
  const rows = useMemo(() => {
    const byLap = new Map<number, Row>();
    for (const line of w.oneStop) {
      for (const p of line.points) {
        if (!byLap.has(p.lap)) byLap.set(p.lap, { lap: p.lap });
        const row = byLap.get(p.lap)!;
        row[line.pair] = p.mean;
        row[`${line.pair}:code`] = p.strategy;
      }
    }
    return [...byLap.values()].sort((a, b) => a.lap - b.lap);
  }, [w]);

  const all = w.oneStop.flatMap((l) => l.points);
  const bestOne = all.reduce((a, b) => (b.mean < a.mean ? b : a), all[0]);
  const yours = all.find((p) => p.strategy === yourStrategy);
  // Focus the axis on the interesting part (the best few places), with round ticks.
  const top = Math.min(Math.max(...all.map((p) => p.mean)), bestOne.mean + 4);
  const step = top - bestOne.mean > 2 ? 1 : 0.5;
  const lo = Math.floor(bestOne.mean / step) * step;
  const hi = Math.min(21, lo + step * Math.max(2, Math.ceil((top - lo) / step)));
  const ticks = Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, i) => +(lo + i * step).toFixed(1));

  return (
    <>
      <ul className="legend" aria-label="Tire choices">
        {w.oneStop.map((l, i) => (
          <li key={l.pair}>
            <i style={{ background: LINE_COLORS[i % LINE_COLORS.length] }} aria-hidden="true" />
            {l.label}
          </li>
        ))}
        <li>
          <i className="legend-best" aria-hidden="true" />
          Best one-stop ({bestOne.strategy})
        </li>
        {yours && (
          <li>
            <i className="legend-yours" aria-hidden="true" />
            Yours
          </li>
        )}
      </ul>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 12, left: -8, bottom: 8 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis
              dataKey="lap"
              type="number"
              domain={[1, totalLaps]}
              tickLine={false}
              axisLine={{ stroke: "var(--rule-strong)" }}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              label={{ value: "Pit lap", position: "insideBottomRight", offset: -2, fill: "var(--muted)", fontSize: 12 }}
              height={34}
            />
            <YAxis
              reversed
              domain={[lo, hi]}
              ticks={ticks}
              allowDataOverflow
              tickFormatter={(v: number) => `P${v}`}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              width={48}
            />
            <Tooltip content={<OneStopTip />} isAnimationActive={false} />
            {w.oneStop.map((l, i) => (
              <Line
                key={l.pair}
                dataKey={l.pair}
                name={l.label}
                stroke={LINE_COLORS[i % LINE_COLORS.length]}
                strokeWidth={2}
                dot={{ r: 2.5, strokeWidth: 0, fill: LINE_COLORS[i % LINE_COLORS.length] }}
                connectNulls
                isAnimationActive={false}
                activeDot={{
                  r: 5,
                  style: { cursor: "pointer" },
                  onClick: (_: unknown, e: unknown) => {
                    const payload = (e as { payload?: Row })?.payload;
                    const code = payload?.[`${l.pair}:code`];
                    if (typeof code === "string") onUse(code);
                  },
                }}
              />
            ))}
            {yours && (
              <ReferenceDot
                x={yours.lap}
                y={yours.mean}
                r={7}
                fill="none"
                stroke="var(--ink)"
                strokeWidth={2}
                ifOverflow="hidden"
              />
            )}
            <ReferenceDot x={bestOne.lap} y={bestOne.mean} r={5} fill="var(--purple)" stroke="var(--panel)" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="visually-hidden">
        Best one-stop strategy: {bestOne.strategy}, average position {bestOne.mean.toFixed(2)}.{" "}
        {w.oneStop
          .map((l) => {
            const b = l.points.reduce((a, p) => (p.mean < a.mean ? p : a), l.points[0]);
            return `${l.label}: best pit lap ${b.lap} (${b.mean.toFixed(2)})`;
          })
          .join("; ")}
        .
      </p>
    </>
  );
}

function TwoStop({
  cells,
  best,
  yourStrategy,
  onUse,
}: {
  cells: WindowCell[];
  best: string | null;
  yourStrategy: string | null;
  onUse: (code: string) => void;
}) {
  const laps1 = [...new Set(cells.map((c) => c.lap1))].sort((a, b) => a - b);
  const laps2 = [...new Set(cells.map((c) => c.lap2))].sort((a, b) => b - a); // Late stops on top.
  const byKey = new Map(cells.map((c) => [`${c.lap1}:${c.lap2}`, c]));
  const min = Math.min(...cells.map((c) => c.mean));
  const span = 2; // Places: anything this far behind the best two-stop shows the lightest shade.
  const shade = (mean: number) => Math.round(100 - Math.min(1, (mean - min) / span) * 88);
  const bestCell = cells.reduce((a, c) => (c.mean < a.mean ? c : a), cells[0]);
  const labelEvery = Math.ceil(laps1.length / 8);

  return (
    <>
      <div className="heat-wrap">
        <span className="heat-ylabel">Second stop</span>
        <div
          className="heat"
          style={{ gridTemplateColumns: `2.6em repeat(${laps1.length}, minmax(0, 1fr))` }}
          role="grid"
          aria-label="Best average position by first and second stop lap"
        >
          {laps2.map((l2, row) => (
            <div key={l2} role="row" style={{ display: "contents" }}>
              <span className="heat-axis" role="rowheader">
                {row % labelEvery === 0 ? l2 : ""}
              </span>
              {laps1.map((l1) => {
                const c = byKey.get(`${l1}:${l2}`);
                if (!c) return <span key={l1} className="heat-empty" role="gridcell" />;
                const isBest = c === bestCell;
                const isYours = c.strategy === yourStrategy;
                return (
                  <button
                    key={l1}
                    type="button"
                    role="gridcell"
                    className="heat-cell"
                    data-best={isBest}
                    data-yours={isYours}
                    style={{ background: `color-mix(in oklab, var(--data) ${shade(c.mean)}%, var(--panel))` }}
                    title={`${c.strategy}: avg. P${c.mean.toFixed(2)}`}
                    aria-label={`Stops on laps ${l1} and ${l2}: best is ${c.strategy}, average position ${c.mean.toFixed(2)}${isBest ? ", best two-stop" : ""}`}
                    onClick={() => onUse(c.strategy)}
                  />
                );
              })}
            </div>
          ))}
          <span />
          {laps1.map((l1, i) => (
            <span key={l1} className="heat-axis heat-axis-x">
              {i % labelEvery === 0 ? l1 : ""}
            </span>
          ))}
        </div>
      </div>
      <div className="heat-foot">
        <span>First stop</span>
        <span className="heat-scale" aria-hidden="true">
          <span>Best ({bestCell.strategy}, P{bestCell.mean.toFixed(2)})</span>
          <i />
          <span>+{span} places or worse</span>
        </span>
      </div>
      {best && best !== bestCell.strategy && (
        <p className="chart-sub">The overall best strategy is a one-stop ({best}).</p>
      )}
    </>
  );
}

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import { ApiError, api, type Mode, type Trace } from "@/lib/api";
import { formatLapRuns, lapRuns } from "@/lib/analysis";
import { COMPOUND_NAMES } from "@/lib/strategy";
import { Spinner } from "./bits";

interface Props {
  raceId: string;
  mode: Mode;
  driver: string;
  /** Current strategy code, or null if it isn't valid yet. */
  strategy: string | null;
  /** Races behind the latest simulation, for the "one of N" label. */
  simulatedRaces?: number;
  seed: number;
  scLaps: number[];
}

type View = "position" | "gap";
type Row = { lap: number } & Record<string, number | null>;

const TIRE_FILL = { S: "var(--tire-s)", M: "var(--tire-m)", H: "var(--tire-h)" } as const;
const GAP_CAP = 60; // Seconds; lapped cars would otherwise flatten everyone else.

/**
 * One simulated race, lap by lap. Starts on a typical race for the strategy
 * (the driver finishes at the median of 200 simulated races); after that,
 * editing the strategy replays the *same* race conditions, so the change you
 * see comes from the strategy alone.
 */
export function RaceTrace({ raceId, mode, driver, strategy, seed, scLaps, simulatedRaces }: Props) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<View>("position");
  const [nonce, setNonce] = useState(0);
  // The scenario being replayed; null asks the API for a typical one.
  const scenario = useRef<number | null>(null);
  const scKey = scLaps.join(",");

  // New race conditions: start again from a typical race.
  useEffect(() => {
    scenario.current = null;
  }, [raceId, mode, driver, seed, scKey]);

  useEffect(() => {
    if (!strategy) return;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await api.trace(
          {
            race_id: raceId,
            driver,
            strategy,
            mode,
            seed,
            scenario: scenario.current ?? undefined,
            safety_car_laps: scLaps,
          },
          ctrl.signal,
        );
        scenario.current = res.scenario;
        setTrace(res);
        setError(null);
      } catch (err) {
        if (ctrl.signal.aborted) return;
        setError(
          err instanceof ApiError && err.status === 429
            ? "The simulator is busy. Try again in a moment."
            : err instanceof Error
              ? err.message
              : "Couldn't load the race.",
        );
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, 200);
    return () => {
      ctrl.abort();
      clearTimeout(t);
    };
    // scLaps is tracked through scKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raceId, mode, driver, strategy, seed, scKey, nonce]);

  const typical = () => {
    scenario.current = null;
    setNonce((n) => n + 1);
  };
  const another = () => {
    let next = Math.floor(Math.random() * 1_000_000);
    if (next === scenario.current) next += 1;
    scenario.current = next;
    setNonce((n) => n + 1);
  };

  const current = trace && trace.race_id === raceId && trace.driver === driver ? trace : null;
  const me = current?.drivers.find((d) => d.code === driver) ?? null;

  const rows = useMemo(() => {
    if (!current) return [];
    const out: Row[] = [];
    for (let lap = 0; lap <= current.total_laps; lap++) {
      const row: Row = { lap };
      for (const d of current.drivers) {
        const v = view === "position" ? d.positions[lap] : d.gaps[lap];
        row[d.code] = v === null || v === undefined ? null : view === "gap" ? Math.min(v, GAP_CAP) : v;
      }
      out.push(row);
    }
    return out;
  }, [current, view]);

  const finishOrder = useMemo(() => {
    const m = new Map<number, string>();
    current?.drivers.forEach((d) => m.set(d.position, d.code));
    return m;
  }, [current]);

  const field = current?.drivers.length ?? 20;
  const forced = new Set(current?.forced_safety_car_laps ?? []);

  return (
    <div aria-busy={loading}>
      <p className="example-label">
        <strong>Example simulated race</strong>, one of{" "}
        {simulatedRaces ? simulatedRaces.toLocaleString() : "thousands of"} possible outcomes with{" "}
        {strategy ?? "this strategy"}. It shows how a result can happen, not the prediction itself: that is the
        typical finish in step 2.
      </p>
      <div>
        <div className="trace-controls">
          <div className="segmented segmented-sm" role="group" aria-label="Chart">
            <button type="button" aria-pressed={view === "position"} onClick={() => setView("position")}>
              Position
            </button>
            <button type="button" aria-pressed={view === "gap"} onClick={() => setView("gap")}>
              Gap to leader
            </button>
          </div>
          <span className="spacer" />
          <button type="button" className="btn btn-sm" onClick={typical} disabled={!strategy}>
            Typical race
          </button>
          <button type="button" className="btn btn-sm" onClick={another} disabled={!strategy}>
            Another race
          </button>
        </div>

        {error && (
          <p className="message" data-tone="error" role="alert">
            {error}
          </p>
        )}
        {!current && !error && (
          <p className="status-line">
            <Spinner /> Simulating a race
          </p>
        )}

        {current && me && (
          <>
            <p className="trace-summary" aria-live="polite">
              {summary(current, driver)}
            </p>
            <p className="chart-sub">
              {current.typical && current.strategy === strategy
                ? `A typical race: P${current.typical.median_position_if_finished} is ${driver}'s median finish over ${current.typical.sample_size} simulated races with this strategy. `
                : `Race #${current.scenario.toLocaleString()}. `}
              Edit the strategy to replay the same race conditions (safety cars, retirements, race-day pace).
            </p>
            <div className="trace-chart" style={{ opacity: loading ? 0.6 : 1 }}>
              <ResponsiveContainer>
                <LineChart data={rows} margin={{ top: 18, right: 8, left: -4, bottom: 0 }}>
                  <CartesianGrid stroke="var(--rule)" vertical={false} />
                  <XAxis
                    dataKey="lap"
                    type="number"
                    domain={[0, current.total_laps]}
                    ticks={lapTicks(current.total_laps)}
                    tickLine={false}
                    axisLine={{ stroke: "var(--rule-strong)" }}
                    tick={{ fill: "var(--muted)", fontSize: 12 }}
                    tickFormatter={(v: number) => (v === 0 ? "Grid" : String(v))}
                  />
                  {view === "position" ? (
                    <>
                      <YAxis
                        yAxisId="l"
                        reversed
                        domain={[1, field]}
                        ticks={[1, 5, 10, 15, 20].filter((t) => t <= field)}
                        tickFormatter={(v: number) => `P${v}`}
                        tickLine={false}
                        axisLine={false}
                        tick={{ fill: "var(--muted)", fontSize: 12 }}
                        width={40}
                      />
                      <YAxis
                        yAxisId="r"
                        orientation="right"
                        reversed
                        domain={[1, field]}
                        ticks={Array.from({ length: field }, (_, i) => i + 1)}
                        interval={0}
                        tickLine={false}
                        axisLine={false}
                        width={40}
                        tick={(p: { x: number; y: number; payload: { value: number } }) => {
                          const code = finishOrder.get(p.payload.value) ?? "";
                          const mine = code === driver;
                          return (
                            <text
                              x={p.x + 4}
                              y={p.y}
                              dy={4}
                              fontSize={11}
                              fontWeight={mine ? 700 : 400}
                              fill={mine ? "var(--ink)" : "var(--muted)"}
                            >
                              {code}
                            </text>
                          );
                        }}
                      />
                    </>
                  ) : (
                    <YAxis
                      yAxisId="l"
                      reversed
                      domain={[0, GAP_CAP]}
                      allowDataOverflow
                      tickFormatter={(v: number) => (v === 0 ? "Leader" : `+${v}s`)}
                      tickLine={false}
                      axisLine={false}
                      tick={{ fill: "var(--muted)", fontSize: 12 }}
                      width={52}
                    />
                  )}
                  <Tooltip
                    content={<TraceTip trace={current} driver={driver} />}
                    isAnimationActive={false}
                    cursor={{ stroke: "var(--rule-strong)" }}
                  />
                  {lapRuns(current.virtual_safety_car_laps ?? []).map(([a, b]) => (
                    <ReferenceArea
                      key={`vsc${a}`}
                      yAxisId="l"
                      x1={a - 1}
                      x2={b}
                      fill="var(--vsc-band)"
                      fillOpacity={1}
                      stroke="none"
                      label={{ value: "VSC", position: "insideTop", offset: -14, fill: "var(--ink-2)", fontSize: 11 }}
                    />
                  ))}
                  {lapRuns(current.safety_car_laps).map(([a, b]) => (
                    <ReferenceArea
                      key={a}
                      yAxisId="l"
                      x1={a - 1}
                      x2={b}
                      fill="var(--sc-band)"
                      fillOpacity={1}
                      stroke="none"
                      label={{
                        value: forced.has(a) ? "SC (what-if)" : "SC",
                        position: "insideTop",
                        offset: -14,
                        fill: "var(--ink-2)",
                        fontSize: 11,
                      }}
                    />
                  ))}
                  {current.drivers
                    .filter((d) => d.code !== driver)
                    .map((d) => (
                      <Line
                        key={d.code}
                        yAxisId="l"
                        dataKey={d.code}
                        stroke="var(--trace-other)"
                        strokeWidth={1}
                        dot={false}
                        activeDot={false}
                        isAnimationActive={false}
                      />
                    ))}
                  {view === "position" && (
                    // Invisible: only gives the right-hand axis (finishing order) a series.
                    <Line
                      yAxisId="r"
                      dataKey={driver}
                      stroke="none"
                      dot={false}
                      activeDot={false}
                      isAnimationActive={false}
                    />
                  )}
                  <Line
                    yAxisId="l"
                    dataKey={driver}
                    stroke="var(--data)"
                    strokeWidth={3}
                    dot={false}
                    activeDot={{ r: 4, fill: "var(--data)", stroke: "var(--panel)" }}
                    isAnimationActive={false}
                  />
                  {me.pits.map((p) => {
                    const y = rows[p.lap]?.[driver];
                    return y === null || y === undefined ? null : (
                      <ReferenceDot
                        key={p.lap}
                        yAxisId="l"
                        x={p.lap}
                        y={y}
                        r={6}
                        fill={TIRE_FILL[p.compound]}
                        stroke="var(--ink)"
                        strokeWidth={1.5}
                        label={{
                          value: `Pit, ${COMPOUND_NAMES[p.compound].toLowerCase()}`,
                          position: y <= 2 && view === "position" ? "bottom" : "top",
                          offset: 10,
                          fill: "var(--ink)",
                          fontSize: 12,
                          stroke: "var(--panel)",
                          strokeWidth: 4,
                          paintOrder: "stroke",
                        }}
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="visually-hidden">
              {driver} position by lap: {me.positions.map((p, i) => `lap ${i} P${p ?? "out"}`).join(", ")}.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function lapTicks(total: number): number[] {
  const out = [0];
  for (let l = 10; l < total - 4; l += 10) out.push(l);
  out.push(total);
  return out;
}

function summary(t: Trace, code: string): string {
  const d = t.drivers.find((x) => x.code === code)!;
  const stops = d.pits.map((p) => `lap ${p.lap} for ${COMPOUND_NAMES[p.compound].toLowerCase()}s`);
  const pits = stops.length ? `pits on ${joinAnd(stops)}` : "doesn't stop";
  const end = d.finished ? `finishes P${d.position}` : `retires on lap ${d.retired_lap}`;
  const vscLaps = t.virtual_safety_car_laps ?? [];
  const sc =
    (t.safety_car_laps.length ? ` Safety car on laps ${formatLapRuns(t.safety_car_laps)}.` : " No safety car.") +
    (vscLaps.length ? ` Virtual safety car on laps ${formatLapRuns(vscLaps)}.` : "");
  const winner = t.drivers.find((x) => x.position === 1 && x.finished);
  const win = winner && winner.code !== code ? ` ${winner.code} wins.` : "";
  return `${code} starts P${d.grid}, ${pits} and ${end}.${sc}${win}`;
}

function joinAnd(parts: string[]): string {
  return parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function TraceTip({
  active,
  label,
  trace,
  driver,
}: Partial<TooltipContentProps<number, string>> & { trace: Trace; driver: string }) {
  if (!active || label === undefined) return null;
  const lap = Number(label);
  const d = trace.drivers.find((x) => x.code === driver)!;
  const pos = d.positions[lap];
  const gap = d.gaps[lap];
  const pit = d.pits.find((p) => p.lap === lap);
  const sc = trace.safety_car_laps.includes(lap);
  const vsc = (trace.virtual_safety_car_laps ?? []).includes(lap);
  const ahead = trace.drivers.find((x) => pos !== null && x.positions[lap] === pos - 1);
  return (
    <div className="tooltip">
      <strong>{lap === 0 ? "Grid" : `Lap ${lap}`}</strong>
      {pos === null ? (
        <span>{driver} retired</span>
      ) : (
        <>
          <span>
            {driver} P{pos}
            {gap ? `, ${gap.toFixed(1)} s behind the leader` : lap > 0 ? ", leading" : ""}
          </span>
          {ahead && ahead.gaps[lap] !== null && gap !== null && (
            <span>
              {(gap - (ahead.gaps[lap] ?? 0)).toFixed(1)} s behind {ahead.code}
            </span>
          )}
          {pit && <span>Pits for {COMPOUND_NAMES[pit.compound].toLowerCase()}s</span>}
          {sc && <span>Safety car</span>}
          {vsc && <span>Virtual safety car</span>}
        </>
      )}
    </div>
  );
}

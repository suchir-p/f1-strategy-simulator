"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Tire } from "@/lib/api";
import {
  COMPOUND_NAMES,
  moveStop,
  setStintCompound,
  stintLength,
  toStints,
  type Compound,
  type Strategy,
} from "@/lib/strategy";

interface Props {
  strategy: Strategy;
  totalLaps: number;
  tires: Record<Compound, Tire | undefined>;
  onChange: (s: Strategy) => void;
  /** Laps forced to run under the safety car (what-if), drawn as a band. */
  scLaps?: number[];
}

const LETTER: Record<Tire["designation"], Compound> = { soft: "S", medium: "M", hard: "H" };
export const compoundOf = (t: Tire) => LETTER[t.designation];

/**
 * The race as a lap-scaled bar. Stints show their tire; the part of a stint
 * that runs longer than any stint in the calibration data is hatched.
 * Pit markers drag (or take arrow keys) to move a stop; clicking a stint
 * cycles its compound.
 */
export function StintTimeline({ strategy, totalLaps, tires, onChange, scLaps = [] }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const dragging = useRef<number | null>(null);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const stints = toStints(strategy, totalLaps);
  const available = (["S", "M", "H"] as Compound[]).filter((c) => tires[c]);
  const pct = (lapBoundary: number) => `${(lapBoundary / totalLaps) * 100}%`;

  const lapFromPointer = (clientX: number) => {
    const rect = trackRef.current!.getBoundingClientRect();
    return ((clientX - rect.left) / rect.width) * totalLaps;
  };

  // The whole marker (line, tag and a 20px hit zone around them) is draggable.
  const onPointerDown = (index: number) => (e: PointerEvent<HTMLDivElement>) => {
    dragging.current = index;
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging.current === null) return;
    onChange(moveStop(strategy, dragging.current, lapFromPointer(e.clientX), totalLaps));
  };
  const onPointerUp = () => {
    dragging.current = null;
  };
  const onKey = (index: number) => (e: KeyboardEvent<HTMLButtonElement>) => {
    const step = e.shiftKey ? 5 : 1;
    const lap = strategy.stops[index].lap;
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
      onChange(moveStop(strategy, index, lap - step, totalLaps));
      e.preventDefault();
    } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
      onChange(moveStop(strategy, index, lap + step, totalLaps));
      e.preventDefault();
    }
  };

  const cycle = (index: number, current: Compound) => {
    const next = available[(available.indexOf(current) + 1) % available.length];
    onChange(setStintCompound(strategy, index, next));
  };

  const ticks: number[] = [1];
  const every = 10;
  for (let lap = every; lap < totalLaps - 3; lap += every) ticks.push(lap);
  ticks.push(totalLaps);

  return (
    <div className="timeline">
      <div className="timeline-track" ref={trackRef}>
        {stints.map((st, i) => {
          const len = stintLength(st);
          const px = (len / totalLaps) * width;
          const tire = tires[st.compound];
          const observed = tire?.max_stint_laps ?? 0;
          const beyond = observed > 0 && len > observed ? len - observed : 0;
          const name = COMPOUND_NAMES[st.compound];
          return (
            <button
              key={i}
              type="button"
              className="stint"
              data-compound={st.compound}
              style={{
                left: `calc(${pct(st.startLap - 1)} + ${i === 0 ? 0 : 2}px)`,
                width: `calc(${pct(len)} - ${i === 0 ? 0 : 2}px)`,
              }}
              onClick={() => cycle(i, st.compound)}
              aria-label={`Stint ${i + 1}: ${name} tires, laps ${st.startLap} to ${st.endLap}${
                beyond ? `, ${beyond} laps longer than any stint in the data` : ""
              }. Activate to change tires.`}
            >
              {px > 88 && <span className="stint-name">{name}</span>}
              {px > 88 && <span className="stint-laps">{len} laps</span>}
              {px <= 88 && px > 22 && <span className="stint-name">{st.compound}</span>}
              {beyond > 0 && (
                <span
                  className="extrapolated"
                  style={{ width: `${(beyond / len) * 100}%` }}
                  aria-hidden="true"
                />
              )}
            </button>
          );
        })}
        {scLaps.length > 0 && (
          <div
            className="sc-band"
            style={{ left: pct(scLaps[0] - 1), width: pct(scLaps[scLaps.length - 1] - scLaps[0] + 1) }}
            aria-hidden="true"
          >
            <span>SC</span>
          </div>
        )}
        {strategy.stops.map((stop, i) => {
          const lo = (i === 0 ? 0 : strategy.stops[i - 1].lap) + 3;
          const hi = (i === strategy.stops.length - 1 ? totalLaps : strategy.stops[i + 1].lap) - 3;
          return (
            <div
              key={i}
              className="pit"
              style={{ left: pct(stop.lap) }}
              data-dragging={dragging.current === i}
              onPointerDown={onPointerDown(i)}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            >
              <div className="pit-line" aria-hidden="true" />
              <button
                type="button"
                className="pit-handle"
                role="slider"
                aria-label={`Pit stop ${i + 1}`}
                aria-valuemin={lo}
                aria-valuemax={hi}
                aria-valuenow={stop.lap}
                aria-valuetext={`End of lap ${stop.lap}`}
                onKeyDown={onKey(i)}
              >
                Lap {stop.lap}
              </button>
            </div>
          );
        })}
      </div>
      <div className="timeline-axis" aria-hidden="true">
        {ticks.map((lap) => (
          <span key={lap} style={{ left: pct(lap - 0.5) }}>
            {lap}
          </span>
        ))}
      </div>
    </div>
  );
}

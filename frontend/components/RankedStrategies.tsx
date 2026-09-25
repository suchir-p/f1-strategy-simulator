"use client";

import { Fragment, useState } from "react";
import type { Candidate, OptimizationResult } from "@/lib/api";
import { MiniStints, WarnIcon } from "./bits";

interface Props {
  result: OptimizationResult;
  totalLaps: number;
  yourStrategy: string | null;
  onUse: (code: string) => void;
}

/** Gap to the best strategy with its paired 95% interval, on a shared scale.
 * Plain positioned elements, so the dot stays round at any column width. */
function GapPlot({ c, max }: { c: Candidate; max: number }) {
  const { diff_vs_best: d, ci95 } = c.comparison_vs_best;
  const x = (v: number) => `${(Math.max(0, Math.min(v, max)) / max) * 100}%`;
  return (
    <span className="gap-plot" aria-hidden="true">
      <span className="gap-zero" />
      {c.rank > 1 && (
        <span className="gap-ci" style={{ left: x(ci95[0]), width: `calc(${x(ci95[1])} - ${x(ci95[0])})` }} />
      )}
      <span className="gap-dot" data-best={c.rank === 1} style={{ left: x(d) }} />
    </span>
  );
}

/** Caveats specific to this strategy (borrowed tires, stints beyond the data).
 * Race-wide ones, like pooled tire estimates, are shown once at the top. */
const isSpecific = (note: string) => /neighboring compound|longer than/.test(note);

function gapText(c: Candidate): string {
  if (c.rank === 1) return "Best";
  const { diff_vs_best: d, ci95, inconclusive } = c.comparison_vs_best;
  const range = `${ci95[0].toFixed(2)} to ${ci95[1].toFixed(2)}`;
  return inconclusive
    ? `+${d.toFixed(2)} places, not distinguishable from best (${range})`
    : `+${d.toFixed(2)} places (${range})`;
}

const COLLAPSED = 3;

export function RankedStrategies({ result, totalLaps, yourStrategy, onUse }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const shown = result.ranked.slice(0, all ? undefined : COLLAPSED);
  const yours = result.compared_strategies.find((c) => c.strategy === yourStrategy);
  if (yours && !shown.some((c) => c.strategy === yours.strategy)) shown.push(yours);
  const max = Math.max(0.5, ...shown.map((c) => c.comparison_vs_best.ci95[1]));
  const more = result.ranked.length > COLLAPSED;

  return (
    <>
    <div className="table-scroll">
      <table className="ranked">
        <thead>
          <tr>
            <th scope="col">Rank</th>
            <th scope="col">Strategy</th>
            <th scope="col">Avg. position</th>
            <th scope="col">Gap to best, with 95% interval</th>
            <th scope="col">
              <span className="visually-hidden">Caveats and actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((c) => {
            const isYours = c.strategy === yourStrategy;
            const expanded = open === c.strategy;
            return (
              <Fragment key={c.strategy}>
                <tr data-best={c.rank === 1} data-yours={isYours}>
                  <td className="rank">{c.rank}</td>
                  <td>
                    <span className="code">{c.strategy}</span>
                    {isYours && <span className="best-tag">Yours</span>}
                    <MiniStints code={c.strategy} totalLaps={totalLaps} />
                  </td>
                  <td className="avg">{c.outcome.mean_scored_position.toFixed(2)}</td>
                  <td className="gap-cell">
                    <GapPlot c={c} max={max} />
                    <span className="gap-text">{gapText(c)}</span>
                  </td>
                  <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                    {c.notes.some(isSpecific) && (
                      <button
                        type="button"
                        className="notes-toggle"
                        aria-expanded={expanded}
                        onClick={() => setOpen(expanded ? null : c.strategy)}
                      >
                        <WarnIcon />
                        {c.notes.filter(isSpecific).length}{" "}
                        {c.notes.filter(isSpecific).length === 1 ? "caveat" : "caveats"}
                      </button>
                    )}
                    {!isYours && (
                      <button type="button" className="btn btn-quiet" onClick={() => onUse(c.strategy)}>
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
                {expanded && (
                  <tr className="row-notes" data-yours={isYours}>
                    <td />
                    <td colSpan={4}>
                      <ul>
                        {c.notes.filter(isSpecific).map((n) => (
                          <li key={n}>{n[0].toUpperCase() + n.slice(1)}</li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
    {more && (
      <button type="button" className="link-btn show-all" aria-expanded={all} onClick={() => setAll(!all)}>
        {all ? `Show the top ${COLLAPSED}` : `Show all ${result.ranked.length} top strategies`}
      </button>
    )}
    </>
  );
}

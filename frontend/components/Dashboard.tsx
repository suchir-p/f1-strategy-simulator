"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  api,
  type Job,
  type Mode,
  type OptimizationResult,
  type Race,
  type RaceDriver,
  type SimulationResult,
  type Tire,
  type Tires,
} from "@/lib/api";
import { comparedSentence, formatLapRuns, lapsFromParam, lapsToParam } from "@/lib/analysis";
import {
  addStop,
  formatStrategy,
  parseStrategy,
  removeStop,
  stintLength,
  toStints,
  validateStrategy,
  COMPOUND_NAMES,
  type Compound,
  type Strategy,
} from "@/lib/strategy";
import { useJob, type JobState } from "@/lib/useJob";
import { ACCURACY_SUMMARY, AccuracySection } from "./AccuracySection";
import { Spinner, TireChip, WarnIcon, pct } from "./bits";
import { PitWindowChart } from "./PitWindowChart";
import { PositionChart } from "./PositionChart";
import { RaceTrace } from "./RaceTrace";
import { RankedStrategies } from "./RankedStrategies";
import { StintTimeline, compoundOf } from "./StintTimeline";
import { TireWearChart } from "./TireWearChart";

const DEFAULT_RACE = "2024-italy";
const SEARCH_DETAIL = { coarse: 4, standard: 3, fine: 2 } as const;
type Detail = keyof typeof SEARCH_DETAIL;
const SEARCH_SCENARIOS = 300;
// Race replays need a seed; without one set, every visitor sees the same races.
const DEFAULT_TRACE_SEED = 2024;

type CompareChoice = "reference" | "best" | "last" | "none";
type AdvancedTab = "replay" | "tires" | "accuracy";
const TABS: { id: AdvancedTab; label: string }[] = [
  { id: "replay", label: "Race replay" },
  { id: "tires", label: "Tire wear" },
  { id: "accuracy", label: "Model accuracy" },
];

function raceShortName(r: Race) {
  return r.name.replace(/^\d{4} /, "").replace(" Grand Prix", "");
}

function useElapsed(state: JobState<unknown>) {
  const [now, setNow] = useState(Date.now());
  const waiting = state.phase === "waiting";
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [waiting]);
  return waiting ? Math.max(0, Math.round((now - state.since) / 1000)) : 0;
}

function JobStatusLine({
  state,
  idle,
  work,
}: {
  state: JobState<unknown>;
  idle: string;
  work?: (job: Job) => string;
}) {
  const elapsed = useElapsed(state);
  if (state.phase === "idle") return <p className="empty">{idle}</p>;
  if (state.phase === "submitting")
    return (
      <p className="status-line" role="status">
        <Spinner /> Sending to the simulator
      </p>
    );
  if (state.phase === "waiting")
    return (
      <p className="status-line" role="status">
        <Spinner />
        {state.job.status === "pending"
          ? "Waiting for a free worker"
          : `${work ? `${work(state.job)}, ` : "Running, "}${elapsed} s`}
      </p>
    );
  if (state.phase === "error")
    return (
      <p className="message" data-tone="error" role="alert">
        {state.message}
      </p>
    );
  return null;
}

function StepTitle({ n, id, children }: { n: number; id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="step-title">
      <span className="step-num" aria-hidden="true">
        {n}
      </span>
      {children}
    </h2>
  );
}

export function Dashboard() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const [races, setRaces] = useState<Race[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drivers, setDrivers] = useState<RaceDriver[]>([]);
  const [tires, setTires] = useState<Tires | null>(null);
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [codeDraft, setCodeDraft] = useState("");
  const [scenarios, setScenarios] = useState(1000);
  const [detail, setDetail] = useState<Detail>("standard");
  const [seedText, setSeedText] = useState(() => params.get("seed") ?? "");
  const [compareChoice, setCompareChoice] = useState<CompareChoice>("reference");
  // Strategy of the run before the latest one, offered as a comparison.
  const [lastRun, setLastRun] = useState<string | null>(null);
  const [tab, setTab] = useState<AdvancedTab | null>(null);
  const [copied, setCopied] = useState(false);
  const advancedRef = useRef<HTMLElement>(null);

  const raceId = params.get("race") ?? DEFAULT_RACE;
  const requestedMode = (params.get("mode") as Mode | null) ?? "pre_race";
  const race = races?.find((r) => r.id === raceId) ?? null;
  const mode: Mode = race && !race.modes[requestedMode].available ? "race_fitted" : requestedMode;
  const driverCode = params.get("driver") ?? drivers[0]?.code ?? null;
  const driver = drivers.find((d) => d.code === driverCode) ?? null;

  // Reads the live URL rather than a captured copy, so delayed updates (the
  // strategy sync below) never undo a newer change such as a driver switch.
  const setParam = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(window.location.search);
      for (const [k, v] of Object.entries(changes)) {
        if (v === null) next.delete(k);
        else next.set(k, v);
      }
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [pathname, router],
  );

  const sim = useJob<SimulationResult>(api.simulationResult);
  const opt = useJob<OptimizationResult>(api.optimizationResult);

  // ---- data loading -----------------------------------------------------------
  useEffect(() => {
    api.races().then(setRaces, (e: Error) => setLoadError(e.message));
  }, []);

  useEffect(() => {
    if (!race) return;
    let live = true;
    setTires(null);
    Promise.all([api.drivers(race.id, mode), api.tires(race.id, mode)]).then(
      ([d, t]) => {
        if (!live) return;
        setDrivers(d);
        setTires(t);
      },
      (e: Error) => live && setLoadError(e.message),
    );
    return () => {
      live = false;
    };
  }, [race, mode]);

  // A new race, mode or driver clears results that no longer apply.
  const { reset: resetSim } = sim;
  const { reset: resetOpt } = opt;
  useEffect(() => {
    resetSim();
    resetOpt();
    setLastRun(null);
  }, [raceId, mode, driverCode, resetSim, resetOpt]);

  // A shared link (?s=M-24-H) sets the first strategy shown; after that, a
  // new driver, race or mode starts from that driver's default.
  const linkStrategy = useRef<string | null>(params.get("s"));
  useEffect(() => {
    if (!driver?.default_strategy || !tires || tires.race_id !== raceId) return;
    const fromLink = linkStrategy.current ? parseStrategy(linkStrategy.current) : null;
    linkStrategy.current = null;
    const s = fromLink ?? parseStrategy(driver.default_strategy);
    setStrategy(s);
    setCodeDraft(s ? formatStrategy(s) : driver.default_strategy);
  }, [driver, tires, raceId]);

  // "How accurate is it?" links (#accuracy) open that tab.
  useEffect(() => {
    const open = () => {
      if (window.location.hash === "#accuracy") setTab("accuracy");
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);

  // ---- derived ------------------------------------------------------------------
  const tireMap = useMemo(() => {
    const m: Record<Compound, Tire | undefined> = { S: undefined, M: undefined, H: undefined };
    tires?.compounds.forEach((t) => (m[compoundOf(t)] = t));
    return m;
  }, [tires]);
  const available = (["S", "M", "H"] as Compound[]).filter((c) => tireMap[c]);
  const totalLaps = race?.total_laps ?? 0;
  const code = strategy ? formatStrategy(strategy) : "";
  const invalid = strategy && race ? validateStrategy(strategy, totalLaps, available) : null;
  const seed = /^\d+$/.test(seedText.trim()) ? Number(seedText.trim()) : undefined;
  const scLaps = useMemo(
    () => lapsFromParam(params.get("sc")).filter((l) => !totalLaps || l < totalLaps),
    [params, totalLaps],
  );
  const scKey = scLaps.join(",");
  const busy = (s: JobState<unknown>) => s.phase === "submitting" || s.phase === "waiting";

  const simResult = sim.state.phase === "done" ? sim.state.result : null;
  const optResult = opt.state.phase === "done" ? opt.state.result : null;
  const optOtherDriver = !!optResult && optResult.driver !== driverCode;

  // Remember the previous run so it can be compared against.
  const latestRun = useRef<string | null>(null);
  useEffect(() => {
    if (!simResult) return;
    if (latestRun.current && latestRun.current !== simResult.strategy) setLastRun(latestRun.current);
    latestRun.current = simResult.strategy;
  }, [simResult]);
  useEffect(() => {
    latestRun.current = null;
  }, [raceId, mode, driverCode]);

  // ---- comparison target ---------------------------------------------------------
  const reference = driver?.default_strategy ?? null;
  const bestFound = optResult && !optOtherDriver ? (optResult.ranked[0]?.strategy ?? null) : null;
  const compareOptions = useMemo(() => {
    const out: { id: CompareChoice; label: string; value: string | null }[] = [];
    if (reference)
      out.push({
        id: "reference",
        label:
          mode === "race_fitted"
            ? `What ${driver?.code} actually ran (${reference})`
            : `The reference strategy (${reference})`,
        value: reference,
      });
    if (bestFound) out.push({ id: "best", label: `The search's best (${bestFound})`, value: bestFound });
    if (lastRun) out.push({ id: "last", label: `Your previous run (${lastRun})`, value: lastRun });
    out.push({ id: "none", label: "Nothing", value: null });
    return out;
  }, [reference, bestFound, lastRun, mode, driver?.code]);
  const compareTo = (compareOptions.find((o) => o.id === compareChoice) ?? compareOptions[0]).value;
  const comparingTo = compareTo && compareTo !== code ? compareTo : null;
  const nameOf = (s: string) => {
    const o = compareOptions.find((x) => x.value === s && x.id !== "none");
    return o ? o.label.replace(/^The /, "the ").replace(/^Your /, "your ") : s;
  };

  // Keep the URL shareable: it carries the strategy and seed being looked at.
  useEffect(() => {
    if (!code) return;
    const t = setTimeout(() => {
      const now = new URLSearchParams(window.location.search);
      const seedParam = seedText.trim() || null;
      if (now.get("s") !== code || now.get("seed") !== seedParam) setParam({ s: code, seed: seedParam });
    }, 400);
    return () => clearTimeout(t);
  }, [code, seedText, setParam]);

  // One consolidated caveat for the strategy being edited.
  const caveat = useMemo(() => {
    if (!strategy || !race) return null;
    const stints = toStints(strategy, totalLaps);
    const used = [...new Set(stints.map((s) => s.compound))];
    const pooled = used.filter((c) => tireMap[c] && ["pooled", "race_low"].includes(tireMap[c]!.confidence));
    const borrowed = used.filter((c) => tireMap[c]?.confidence === "fallback");
    const long = stints.filter((st) => {
      const t = tireMap[st.compound];
      return t && t.max_stint_laps > 0 && stintLength(st) > t.max_stint_laps;
    });
    const list = (cs: Compound[]) =>
      cs
        .map((c, i) => (i === 0 ? COMPOUND_NAMES[c] : COMPOUND_NAMES[c].toLowerCase()))
        .join(cs.length === 2 ? " and " : ", ");
    const details: string[] = [];
    if (pooled.length)
      details.push(
        `${list(pooled)} wear ${pooled.length > 1 ? "are" : "is"} estimated from the same Pirelli compound at other races, not measured here.`,
      );
    if (borrowed.length)
      details.push(
        `${list(borrowed)} ${borrowed.length > 1 ? "have" : "has"} too little data at any race, so ${borrowed.length > 1 ? "their" : "its"} wear is borrowed from a neighboring compound and made deliberately pessimistic.`,
      );
    long.forEach((st) => {
      const t = tireMap[st.compound]!;
      details.push(
        `Laps ${st.startLap}–${st.endLap}: a ${stintLength(st)}-lap stint on ${COMPOUND_NAMES[st.compound].toLowerCase()}s is longer than any in the data (${t.max_stint_laps} laps). The hatched part of the bar is extrapolated.`,
      );
    });
    if (!details.length) return null;
    const severe = borrowed.length > 0 || long.length > 0;
    return {
      severe,
      title: severe ? "This strategy goes beyond the tire data" : "Tire estimates have limited data",
      short: severe
        ? long.length
          ? `A stint runs longer than any stint the wear model was fitted on.`
          : `${list(borrowed)} wear is borrowed from another compound.`
        : `${list(pooled)} wear ${pooled.length > 1 ? "estimates come" : "estimate comes"} from other races.`,
      details,
    };
  }, [strategy, race, totalLaps, tireMap]);

  // ---- actions ------------------------------------------------------------------
  const update = (s: Strategy) => {
    setStrategy(s);
    setCodeDraft(formatStrategy(s));
  };

  const simulate = () => {
    if (!race || !driver || !strategy || invalid) return;
    sim.run(() =>
      api.submitSimulation({
        race_id: race.id,
        driver: driver.code,
        strategy: code,
        mode,
        scenarios,
        seed,
        safety_car_laps: scLaps,
        compare_with: comparingTo ?? undefined,
      }),
    );
  };

  const optimize = () => {
    if (!race || !driver) return;
    opt.run(() =>
      api.submitOptimization({
        race_id: race.id,
        driver: driver.code,
        mode,
        scenarios: SEARCH_SCENARIOS,
        seed,
        safety_car_laps: scLaps,
        search: { lap_step: SEARCH_DETAIL[detail], max_candidates: 3000, one_stop: true, two_stop: true },
        top_k: 10,
        compare_strategies: strategy && !invalid ? [code] : [],
      }),
    );
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* Clipboard blocked: the address bar still has the link. */
    }
  };

  const openTab = (t: AdvancedTab) => {
    setTab(t);
    requestAnimationFrame(() => advancedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const setScLaps = (laps: number[]) => setParam({ sc: lapsToParam(laps) });
  const scStart = scLaps[0] ?? 0;
  const scLength = scLaps.length || 3;

  // ---- render ---------------------------------------------------------------------
  if (loadError && !races)
    return (
      <main className="shell">
        <h1>F1 strategy simulator</h1>
        <p className="message" data-tone="error" role="alert">
          {loadError} Start it with <code>python -m uvicorn app.main:app</code> from the backend folder.
        </p>
      </main>
    );

  // The editor can move on after a run finishes; say so instead of letting
  // old numbers sit next to a different setup.
  const simOtherDriver = !!simResult && simResult.driver !== driverCode;
  const simScKey = (simResult?.safety_car_laps ?? []).join(",");
  const simSetupStale =
    !!simResult && (simOtherDriver || simResult.strategy !== code || simScKey !== scKey);
  const simCompareStale =
    !!simResult && !simSetupStale && (simResult.comparison?.strategy ?? null) !== comparingTo;
  const optYours = optResult?.compared_strategies[0]?.strategy ?? null;
  const optStale = !optOtherDriver && !!optYours && optYours !== code;
  const optScStale = !!optResult && !optOtherDriver && (optResult.safety_car_laps ?? []).join(",") !== scKey;
  const describe = (drv: string | null, strat: string, sc: number[]) =>
    `${simOtherDriver && drv ? `${drv} on ` : ""}${strat}${sc.length ? ` with a safety car on laps ${formatLapRuns(sc)}` : ""}`;
  const prov = tires?.provenance;

  return (
    <main className="shell">
      <header className="topbar">
        <h1>F1 strategy simulator</h1>
        <a
          className="top-link"
          href="#accuracy"
          onClick={(e) => {
            e.preventDefault();
            openTab("accuracy");
          }}
        >
          Model accuracy: {ACCURACY_SUMMARY.spearman.toFixed(2)} rank correlation ·{" "}
          {ACCURACY_SUMMARY.mae.toFixed(1)} places avg. error
        </a>
        <button type="button" className="btn btn-sm" onClick={copyLink} aria-live="polite">
          {copied ? "Link copied" : "Copy link"}
        </button>
        <label className="field">
          Race
          <select
            className="select"
            value={raceId}
            onChange={(e) => setParam({ race: e.target.value, driver: null, s: null, sc: null })}
            disabled={!races}
          >
            {races?.map((r) => (
              <option key={r.id} value={r.id}>
                Round {r.round}, {raceShortName(r)}
              </option>
            ))}
          </select>
        </label>
        <div className="segmented" role="group" aria-label="Calibration mode">
          <button
            type="button"
            aria-pressed={mode === "pre_race"}
            disabled={!race?.modes.pre_race.available}
            title={race?.modes.pre_race.available ? undefined : race?.modes.pre_race.reason ?? undefined}
            onClick={() => setParam({ mode: "pre_race", s: null })}
          >
            Pre-race
          </button>
          <button
            type="button"
            aria-pressed={mode === "race_fitted"}
            onClick={() => setParam({ mode: "race_fitted", s: null })}
          >
            Retrospective
          </button>
        </div>
      </header>

      {race && (
        <section className="context" aria-label="What these results are based on">
          <span className="race-name">
            {race.name}, {race.total_laps} laps
          </span>
          <span className="mode-note" data-mode={mode}>
            <span className="mode-badge" data-mode={mode}>
              {mode === "pre_race" ? "Pre-race recommendation" : "Retrospective analysis"}
            </span>
            {mode === "pre_race"
              ? `Built from ${prov?.trained_on?.length ?? "earlier"} earlier races, qualifying and the grid. Rivals run ${prov?.rival_strategy?.label ?? "a reference strategy"}.`
              : "Fitted on this race's own results. Rivals run the strategies they actually used."}
            {requestedMode === "pre_race" && mode === "race_fitted" && " No pre-race data this early in the season."}
          </span>
          {scLaps.length > 0 && (
            <span className="whatif" role="status">
              <span className="whatif-badge">What-if</span>
              Safety car on laps {formatLapRuns(scLaps)} in every simulated race.{" "}
              <button type="button" className="link-btn" onClick={() => setScLaps([])}>
                Clear
              </button>
            </span>
          )}
          {tires && (
            <span className="tires" aria-label="Tires this weekend">
              {[...tires.compounds].reverse().map((t) => (
                <TireChip key={t.designation} tire={t} />
              ))}
            </span>
          )}
        </section>
      )}

      <div className="layout">
        <nav className="panel tower" aria-label="Drivers">
          <h2>Starting grid</h2>
          <ol>
            {drivers.map((d) => (
              <li key={d.code}>
                <button
                  type="button"
                  aria-pressed={d.code === driverCode}
                  onClick={() => setParam({ driver: d.code, s: null })}
                >
                  <span className="pos">P{d.grid_position}</span>
                  <span>{d.code}</span>
                  {mode === "race_fitted" && (
                    <span className="actual">
                      {d.actual_position && d.actual_status !== "Retired" ? `fin. P${d.actual_position}` : "DNF"}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div>
          {/* ---- 1. build ---------------------------------------------------------- */}
          <section className="panel step" aria-labelledby="builder-title">
            <div className="panel-head">
              <StepTitle n={1} id="builder-title">
                Build {driver ? `${driver.code}'s` : "a"} strategy
              </StepTitle>
              {driver && (
                <p>
                  Starts P{driver.grid_position}.{" "}
                  {mode === "race_fitted" && driver.actual_strategy
                    ? `Actually ran ${driver.actual_strategy} and ${
                        driver.actual_status === "Retired" ? "retired" : `finished P${driver.actual_position}`
                      }.`
                    : mode === "pre_race"
                      ? "Starting point: the reference strategy."
                      : ""}
                </p>
              )}
            </div>
            <div className="panel-body">
              {strategy && race && tires ? (
                <>
                  <StintTimeline
                    strategy={strategy}
                    totalLaps={totalLaps}
                    tires={tireMap}
                    onChange={update}
                    scLaps={scLaps}
                  />
                  <div className="edit-row">
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => update(addStop(strategy, totalLaps, available))}
                      disabled={strategy.stops.length >= 3}
                    >
                      Add stop
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => update(removeStop(strategy, strategy.stops.length - 1))}
                      disabled={strategy.stops.length <= 1}
                    >
                      Remove last stop
                    </button>
                    <label className="field">
                      <span className="visually-hidden">Strategy code</span>
                      <input
                        className="code-input"
                        value={codeDraft}
                        aria-invalid={parseStrategy(codeDraft) === null}
                        aria-describedby="code-help"
                        onChange={(e) => {
                          setCodeDraft(e.target.value);
                          const s = parseStrategy(e.target.value);
                          if (s) setStrategy(s);
                        }}
                        spellCheck={false}
                      />
                    </label>
                    <span className="timeline-hint">Drag a pit marker to move a stop. Select a stint to change tires.</span>
                  </div>
                  <p id="code-help" className="visually-hidden">
                    Strategy code: starting compound, then pit lap and new compound, for example M-24-H.
                  </p>
                  {invalid && (
                    <p className="message" data-tone="error">
                      {invalid}
                    </p>
                  )}
                  {caveat && (
                    <details className="caveat-box" data-severe={caveat.severe}>
                      <summary>
                        <WarnIcon />
                        <span>
                          <strong>{caveat.title}.</strong> {caveat.short} <span className="more">Details</span>
                        </span>
                      </summary>
                      <ul>
                        {caveat.details.map((d) => (
                          <li key={d}>{d}</li>
                        ))}
                      </ul>
                      <button type="button" className="link-btn" onClick={() => openTab("tires")}>
                        See the tire wear chart
                      </button>
                    </details>
                  )}
                  <div className="action-row">
                    <details className="settings">
                      <summary>Run settings and what-ifs</summary>
                      <div className="settings-grid">
                        <label className="field">
                          What if a safety car comes out
                          <span className="field-pair">
                            <select
                              className="select"
                              aria-label="Safety car from lap"
                              value={scStart}
                              onChange={(e) => {
                                const start = Number(e.target.value);
                                setScLaps(
                                  start
                                    ? Array.from({ length: Math.min(scLength, totalLaps - start) }, (_, i) => start + i)
                                    : [],
                                );
                              }}
                            >
                              <option value={0}>Only at random</option>
                              {Array.from({ length: Math.max(0, totalLaps - 3) }, (_, i) => i + 2).map((lap) => (
                                <option key={lap} value={lap}>
                                  On lap {lap}
                                </option>
                              ))}
                            </select>
                            <select
                              className="select"
                              aria-label="Safety car length"
                              value={scLength}
                              disabled={!scStart}
                              onChange={(e) => {
                                const n = Number(e.target.value);
                                setScLaps(
                                  Array.from({ length: Math.min(n, totalLaps - scStart) }, (_, i) => scStart + i),
                                );
                              }}
                            >
                              {[2, 3, 4, 5].map((n) => (
                                <option key={n} value={n}>
                                  for {n} laps
                                </option>
                              ))}
                            </select>
                          </span>
                        </label>
                        <label className="field">
                          Races per simulation
                          <select
                            className="select"
                            value={scenarios}
                            onChange={(e) => setScenarios(Number(e.target.value))}
                          >
                            <option value={1000}>1,000</option>
                            <option value={2000}>2,000</option>
                            <option value={5000}>5,000</option>
                          </select>
                        </label>
                        <label className="field">
                          Search pit laps
                          <select className="select" value={detail} onChange={(e) => setDetail(e.target.value as Detail)}>
                            <option value="coarse">Every 4 laps (fastest)</option>
                            <option value="standard">Every 3 laps</option>
                            <option value="fine">Every 2 laps (slowest)</option>
                          </select>
                        </label>
                        <label className="field">
                          Seed
                          <input
                            type="text"
                            inputMode="numeric"
                            placeholder="Random"
                            value={seedText}
                            onChange={(e) => setSeedText(e.target.value)}
                          />
                        </label>
                      </div>
                    </details>
                    <span className="spacer" />
                    <button type="button" className="btn" onClick={optimize} disabled={busy(opt.state) || !driver}>
                      Find the best strategy
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-lg"
                      onClick={simulate}
                      disabled={!!invalid || busy(sim.state)}
                    >
                      {comparingTo ? "Simulate and compare" : "Simulate this strategy"}
                    </button>
                  </div>
                </>
              ) : (
                <p className="status-line">
                  <Spinner /> Loading race data
                </p>
              )}
            </div>
          </section>

          <div className="results">
            {/* ---- 2. outcome --------------------------------------------------- */}
            <section className="panel step" aria-labelledby="sim-title" aria-busy={busy(sim.state)}>
              <div className="panel-head">
                <StepTitle n={2} id="sim-title">
                  Your predicted outcome
                </StepTitle>
                <label className="field compare-field">
                  Compared with
                  <select
                    className="select"
                    value={compareOptions.some((o) => o.id === compareChoice) ? compareChoice : compareOptions[0].id}
                    onChange={(e) => setCompareChoice(e.target.value as CompareChoice)}
                  >
                    {compareOptions.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="panel-body">
                <JobStatusLine
                  state={sim.state}
                  idle={`Simulate ${code || "the strategy"} over ${scenarios.toLocaleString()} races with random safety cars, retirements and race-day pace${comparingTo ? `, side by side with ${comparingTo} on the same races` : ""}.`}
                  work={(job) =>
                    `Simulating ${(job.params.scenarios * (job.params.compare_with ? 2 : 1)).toLocaleString()} races`
                  }
                />
                {simResult && simSetupStale && (
                  <p className="stale-note" role="status">
                    These results are for {describe(simResult.driver, simResult.strategy, simResult.safety_car_laps ?? [])}.
                    Simulate again to see {describe(driverCode, code || "your new strategy", scLaps)}.
                  </p>
                )}
                {simResult && simCompareStale && (
                  <p className="stale-note" role="status">
                    {comparingTo
                      ? `Simulate again to compare with ${comparingTo}.`
                      : "Showing the last comparison; simulate again to drop it."}
                  </p>
                )}
                {simResult && (
                  <div className={simSetupStale ? "stale" : undefined}>
                    <div className="outcome">
                      <div>
                        <div className="hero">P{simResult.outcome.p50_if_finished}</div>
                        <div className="hero-label">Typical finish</div>
                      </div>
                      <dl className="outcome-facts">
                        <dt>Win</dt>
                        <dt>Podium</dt>
                        <dt>Points</dt>
                        <dd>{pct(simResult.outcome.p_win)}</dd>
                        <dd>{pct(simResult.outcome.p_podium)}</dd>
                        <dd>{pct(simResult.outcome.p_points)}</dd>
                      </dl>
                    </div>
                    {simResult.comparison && (
                      <p
                        className="compare-verdict"
                        data-decided={simResult.comparison.ci95[1] < 0 || simResult.comparison.ci95[0] > 0}
                      >
                        {comparedSentence(
                          nameOf(simResult.comparison.strategy),
                          simResult.comparison.diff,
                          simResult.comparison.ci95,
                        )}
                      </p>
                    )}
                    <p className="summary-sentence">
                      {simResult.driver} finishes {pct(simResult.outcome.finish_probability)} of races with{" "}
                      {simResult.strategy}. When they finish, it is between P{simResult.outcome.p05_if_finished} and P
                      {simResult.outcome.p95_if_finished} in 90% of races, averaging P
                      {simResult.outcome.mean_position_if_finished.toFixed(1)}.
                    </p>
                    <h3 className="chart-title">Finishing position, if they finish</h3>
                    <PositionChart
                      distribution={simResult.outcome.position_distribution_if_finished}
                      label={simResult.strategy}
                      compare={
                        simResult.comparison
                          ? {
                              label: simResult.comparison.strategy,
                              distribution: simResult.comparison.outcome.position_distribution_if_finished,
                            }
                          : null
                      }
                      actualPosition={
                        mode === "race_fitted" && driver?.actual_status !== "Retired" ? driver?.actual_position : null
                      }
                    />
                    {simResult.comparison && <ComparisonTable result={simResult} />}
                    {simResult.notes.length > 0 && !simSetupStale && caveat && (
                      <p className="result-caveat">
                        <WarnIcon />
                        <span>{caveat.title}: see the note in step 1.</span>
                      </p>
                    )}
                    <p className="meta">
                      <span>{simResult.metadata.mode_label}</span>
                      <span>
                        {simResult.metadata.scenarios.toLocaleString()} races
                        {simResult.comparison ? " per strategy, the same for both" : ""}
                      </span>
                      {simResult.safety_car_laps?.length > 0 && (
                        <span>Safety car forced on laps {formatLapRuns(simResult.safety_car_laps)}</span>
                      )}
                      <span>
                        Seed {simResult.metadata.seed}{" "}
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => setSeedText(String(simResult.metadata.seed))}
                        >
                          Reuse
                        </button>
                      </span>
                      <span>Engine {simResult.metadata.engine_version}</span>
                      <span>Data {simResult.metadata.data_version}</span>
                    </p>
                  </div>
                )}
              </div>
            </section>

            {/* ---- 3. explore --------------------------------------------------- */}
            <section className="panel step" aria-labelledby="opt-title" aria-busy={busy(opt.state)}>
              <div className="panel-head">
                <StepTitle n={3} id="opt-title">
                  Explore strategies
                </StepTitle>
                {optResult && (
                  <p>
                    {optResult.candidates_evaluated.toLocaleString()} strategies, {optResult.metadata.scenarios} races
                    each, on the same races
                  </p>
                )}
              </div>
              <div className="panel-body">
                <JobStatusLine
                  state={opt.state}
                  idle={`"Find the best strategy" tries every one- and two-stop strategy for ${driver?.code ?? "this driver"} on the same simulated races and ranks them, with yours alongside.`}
                  work={(job) =>
                    `Simulating ${Number(job.params.candidates ?? 0).toLocaleString()} strategies × ${job.params.scenarios} races`
                  }
                />
                {optResult && race && optOtherDriver && (
                  <p className="stale-note" role="status">
                    This ranking is for {optResult.driver}. Search again to rank strategies for {driverCode}.
                  </p>
                )}
                {optResult && race && optStale && (
                  <p className="stale-note" role="status">
                    &ldquo;Yours&rdquo; is the strategy you had when you searched ({optYours}). The ranking itself does
                    not change; search again to rank {code || "your new strategy"}.
                  </p>
                )}
                {optResult && race && optScStale && (
                  <p className="stale-note" role="status">
                    This ranking was run{" "}
                    {optResult.safety_car_laps?.length
                      ? `with a safety car on laps ${formatLapRuns(optResult.safety_car_laps)}`
                      : "without the safety-car what-if"}
                    . Search again to apply the current setting.
                  </p>
                )}
                {optResult && race && (
                  <div className={optOtherDriver ? "stale" : undefined}>
                    <PitWindowChart
                      result={optResult}
                      totalLaps={totalLaps}
                      yourStrategy={code}
                      onUse={(c) => {
                        const s = parseStrategy(c);
                        if (s) update(s);
                      }}
                    />
                    <h3 className="chart-title">Top strategies</h3>
                    <RankedStrategies
                      result={optResult}
                      totalLaps={totalLaps}
                      yourStrategy={optResult.compared_strategies[0]?.strategy ?? null}
                      onUse={(c) => {
                        const s = parseStrategy(c);
                        if (s) update(s);
                      }}
                    />
                    <p className="meta">
                      <span>{optResult.metadata.mode_label}</span>
                      <span>Average position counts a retirement as P21</span>
                      <span>Seed {optResult.metadata.seed}</span>
                      {optResult.safety_car_laps?.length > 0 && (
                        <span>Safety car forced on laps {formatLapRuns(optResult.safety_car_laps)}</span>
                      )}
                      <span>Data {optResult.metadata.data_version}</span>
                    </p>
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>

      {/* ---- advanced analysis ---------------------------------------------------- */}
      <section className="panel advanced" id="advanced" ref={advancedRef} aria-labelledby="advanced-title">
        <div className="panel-head">
          <h2 id="advanced-title">Advanced analysis</h2>
          <p>A single race lap by lap, the tire model, and how well the model predicts real races</p>
        </div>
        <div className="tabs" role="tablist" aria-label="Advanced analysis">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              onClick={() => setTab(tab === t.id ? null : t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {tab && (
          <div className="panel-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === "replay" &&
              (race && driver ? (
                <RaceTrace
                  raceId={race.id}
                  mode={mode}
                  driver={driver.code}
                  strategy={strategy && !invalid ? code : null}
                  seed={seed ?? DEFAULT_TRACE_SEED}
                  scLaps={scLaps}
                  simulatedRaces={simResult?.metadata.scenarios}
                />
              ) : (
                <p className="status-line">
                  <Spinner /> Loading race data
                </p>
              ))}
            {tab === "tires" && tires && race && (
              <div className="tires-tab">
                <TireWearChart tires={tireMap} strategy={strategy} totalLaps={totalLaps} />
              </div>
            )}
            {tab === "accuracy" && <AccuracySection />}
          </div>
        )}
      </section>
    </main>
  );
}

function ComparisonTable({ result }: { result: SimulationResult }) {
  const c = result.comparison!;
  const a = result.outcome;
  const b = c.outcome;
  const rows: [string, (o: typeof a) => string][] = [
    ["Average position, scored", (o) => o.mean_scored_position.toFixed(2)],
    ["Typical finish", (o) => `P${o.p50_if_finished}`],
    ["Finishes the race", (o) => pct(o.finish_probability)],
    ["Win", (o) => pct(o.p_win)],
    ["Podium", (o) => pct(o.p_podium)],
    ["Points", (o) => pct(o.p_points)],
  ];
  return (
    <details className="compare-details">
      <summary>Side-by-side numbers</summary>
      <table className="compare-table">
        <caption className="visually-hidden">
          {result.strategy} and {c.strategy} on the same {result.metadata.scenarios.toLocaleString()} simulated races
        </caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Measure</span>
            </th>
            <th scope="col">
              <i className="swatch" style={{ background: "var(--data)" }} aria-hidden="true" />
              {result.strategy}
            </th>
            <th scope="col">
              <i className="swatch" style={{ background: "var(--data-2)" }} aria-hidden="true" />
              {c.strategy}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, f]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{f(a)}</td>
              <td>{f(b)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

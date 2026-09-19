// Typed client for the FastAPI backend (reached through the /api rewrite).

export type Mode = "pre_race" | "race_fitted";

export interface ModeAvailability {
  available: boolean;
  label: string;
  reason: string | null;
}

export interface Race {
  id: string;
  season: number;
  round: number;
  name: string;
  total_laps: number;
  tire_nomination: Record<"hard" | "medium" | "soft", string>;
  note: string;
  modes: Record<Mode, ModeAvailability>;
}

export interface RaceDriver {
  code: string;
  grid_position: number;
  default_strategy: string | null;
  actual_strategy: string | null;
  actual_position: number | null;
  actual_status: string | null;
}

export type Confidence = "race" | "race_low" | "pooled" | "fallback";

export interface Tire {
  designation: "soft" | "medium" | "hard";
  absolute_compound: string;
  confidence: Confidence;
  clean_laps: number | null;
  max_stint_laps: number;
  pace_offset: number;
  deg_linear: number;
  deg_quadratic: number;
}

export interface Provenance {
  mode: Mode;
  trained_on?: string[];
  rival_strategy?: { label: string; method: string };
  event_measured_track_constants?: string[];
  description?: string;
  [key: string]: unknown;
}

export interface Tires {
  race_id: string;
  mode: Mode;
  mode_label: string;
  data_version: string;
  compounds: Tire[];
  provenance: Provenance;
}

export type JobStatus = "pending" | "running" | "completed" | "failed";

export interface Job {
  id: string;
  kind: "simulation" | "optimization";
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  params: Record<string, unknown> & { seed: number; scenarios: number };
  error: { kind: string; message: string } | null;
}

export interface Outcome {
  finish_probability: number;
  mean_position_if_finished: number;
  p05_if_finished: number;
  p50_if_finished: number;
  p95_if_finished: number;
  position_distribution_if_finished: number[];
  p_win: number;
  p_podium: number;
  p_points: number;
  mean_scored_position: number;
}

export interface ResultMetadata {
  calibration_mode: Mode;
  mode_label: string;
  engine_version: string;
  data_version: string;
  seed: number;
  scenarios: number;
  tires: Pick<Tire, "designation" | "absolute_compound" | "confidence" | "clean_laps" | "max_stint_laps">[];
  provenance: Provenance;
}

export interface PairedComparison {
  strategy: string;
  outcome: Outcome;
  notes: string[];
  /** Simulated strategy minus this one, in average position (negative = better). */
  diff: number;
  ci95: [number, number];
}

export interface SimulationResult {
  kind: "simulation";
  job_id: string;
  driver: string;
  strategy: string;
  outcome: Outcome;
  notes: string[];
  safety_car_laps: number[];
  comparison: PairedComparison | null;
  metadata: ResultMetadata;
}

export interface Candidate {
  rank: number;
  strategy: string;
  outcome: Outcome;
  comparison_vs_best: { diff_vs_best: number; ci95: [number, number]; inconclusive: boolean };
  notes: string[];
}

export interface OptimizationResult {
  kind: "optimization";
  job_id: string;
  driver: string;
  candidates_evaluated: number;
  ranked: Candidate[];
  compared_strategies: Candidate[];
  /** Every candidate: [strategy, average position, finish probability]. */
  landscape: [string, number, number][];
  safety_car_laps: number[];
  metadata: ResultMetadata;
}

export interface TraceDriver {
  code: string;
  grid: number;
  strategy: string;
  position: number;
  finished: boolean;
  retired_lap: number | null;
  /** Index 0 = grid, then after each lap; null once retired. */
  positions: (number | null)[];
  gaps: (number | null)[];
  pits: { lap: number; compound: "S" | "M" | "H" }[];
}

export interface Trace {
  race_id: string;
  driver: string;
  strategy: string;
  seed: number;
  scenario: number;
  typical: { sample_size: number; median_position_if_finished: number } | null;
  total_laps: number;
  forced_safety_car_laps: number[];
  safety_car_laps: number[];
  virtual_safety_car_laps?: number[];
  drivers: TraceDriver[];
  mode_label: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Can't reach the simulator API. Check that the backend is running.", 0);
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
      else if (body.detail?.error?.message) detail = body.detail.error.message;
    } catch {
      /* Non-JSON error body: keep the generic message. */
    }
    if (res.status === 502 || res.status === 503 || res.status === 504 || res.status === 500) {
      detail = "Can't reach the simulator API. Check that the backend is running.";
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  races: () => request<Race[]>("/races"),
  drivers: (raceId: string, mode: Mode) => request<RaceDriver[]>(`/races/${raceId}/drivers?mode=${mode}`),
  tires: (raceId: string, mode: Mode) => request<Tires>(`/races/${raceId}/tires?mode=${mode}`),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  simulationResult: (id: string) => request<SimulationResult>(`/jobs/${id}/result`),
  optimizationResult: (id: string) => request<OptimizationResult>(`/jobs/${id}/result`),
  submitSimulation: (body: {
    race_id: string;
    driver: string;
    strategy: string;
    mode: Mode;
    scenarios: number;
    seed?: number;
    safety_car_laps?: number[];
    compare_with?: string;
  }) => request<{ job_id: string }>("/simulations", { method: "POST", body: JSON.stringify(body) }),
  submitOptimization: (body: {
    race_id: string;
    driver: string;
    mode: Mode;
    scenarios: number;
    seed?: number;
    search: { lap_step: number; max_candidates: number; one_stop: boolean; two_stop: boolean };
    top_k: number;
    compare_strategies: string[];
    safety_car_laps?: number[];
  }) => request<{ job_id: string }>("/optimizations", { method: "POST", body: JSON.stringify(body) }),
  trace: (
    body: {
      race_id: string;
      driver: string;
      strategy: string;
      mode: Mode;
      seed: number;
      scenario?: number;
      safety_car_laps?: number[];
    },
    signal?: AbortSignal,
  ) => request<Trace>("/traces", { method: "POST", body: JSON.stringify(body), signal }),
};

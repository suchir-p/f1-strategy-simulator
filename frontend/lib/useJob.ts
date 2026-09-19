"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type Job } from "./api";

export type JobState<R> =
  | { phase: "idle" }
  | { phase: "submitting" }
  | { phase: "waiting"; job: Job; since: number }
  | { phase: "done"; job: Job; result: R }
  | { phase: "error"; message: string; job?: Job };

const POLL_MS = 500;

/**
 * Submits a job, polls its status until it finishes, then loads the result.
 * Starting a new job abandons the previous one's polling (the job itself
 * keeps running on the server).
 */
export function useJob<R>(loadResult: (id: string) => Promise<R>) {
  const [state, setState] = useState<JobState<R>>({ phase: "idle" });
  const generation = useRef(0);

  useEffect(() => () => void generation.current++, []);

  const run = useCallback(
    async (submit: () => Promise<{ job_id: string }>) => {
      const gen = ++generation.current;
      const live = () => gen === generation.current;
      setState({ phase: "submitting" });
      try {
        const { job_id } = await submit();
        const since = Date.now();
        for (;;) {
          const job = await api.job(job_id);
          if (!live()) return;
          if (job.status === "completed") {
            const result = await loadResult(job_id);
            if (live()) setState({ phase: "done", job, result });
            return;
          }
          if (job.status === "failed") {
            setState({ phase: "error", message: job.error?.message ?? "The job failed.", job });
            return;
          }
          setState({ phase: "waiting", job, since });
          await new Promise((r) => setTimeout(r, POLL_MS));
          if (!live()) return;
        }
      } catch (err) {
        if (!live()) return;
        const message =
          err instanceof ApiError && err.status === 429
            ? err.message.startsWith("too many requests")
              ? `You've started a lot of runs in the last minute: ${err.message.replace(/^too many requests; /, "")}.`
              : "The simulator is busy with other jobs. Try again in a few seconds."
            : err instanceof Error
              ? err.message
              : "Something went wrong.";
        setState({ phase: "error", message });
      }
    },
    [loadResult],
  );

  const reset = useCallback(() => {
    generation.current++;
    setState({ phase: "idle" });
  }, []);

  return { state, run, reset };
}

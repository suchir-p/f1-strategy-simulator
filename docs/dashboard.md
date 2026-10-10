# Dashboard

Next.js + TypeScript app that drives the API (requests go through a Next.js
rewrite, so the browser stays same-origin).

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000, with the API running on :8000
```

The page follows one workflow: **1. Build a strategy → 2. See the predicted
outcome → 3. Explore alternatives**, with deeper analysis one click away.

* **1. Build:** the race drawn as a lap-scaled bar in Pirelli's tire colors.
  Drag a pit marker (or use the arrow keys) to move a stop, select a stint to
  change its compound, or type a code like `M-24-H`. Stints longer than any in
  the calibration data are hatched, and one collapsible note summarizes the
  tire-data caveats for the strategy being edited. A safety-car what-if lives
  under "Run settings and what-ifs" and applies to everything below.
* **2. Outcome:** typical finish and win/podium/points odds, compared by
  default with the reference strategy (or the actual one, in retrospective
  mode), the search's best, or your previous run, all on the same simulated
  races: a paired difference with its 95% interval ("no clear difference" when
  it includes zero) and overlaid finishing distributions.
* **3. Explore:** a pit-window chart (average finish by pit lap per tire pair,
  or a heatmap over both stop laps; select a point to load it) and the top
  three strategies with their gap to the best, expandable to ten, with your
  own ranked alongside.
* **Advanced analysis** (tabs, collapsed by default): a race replay labeled as
  one example outcome, which replays the same race conditions as you edit; the
  tire-wear curves, solid where fitted on real stints and dashed beyond; and the
  model's accuracy on held-out and leave-one-out races, also summarized in the
  header.
* **Honest framing and sharing:** every view carries its mode, the rivals'
  strategy, the seed and the data version; pre-race views never show how the
  race ended; the URL carries the race, driver, strategy, seed and what-if.

Tests: `npm test` (strategy and chart logic, Vitest) and `npm run e2e`
(Playwright against the real Next.js app and API: editing, validation,
simulate, compare, search, race replay, the what-if, shared links and the
pre-race/retrospective rules).

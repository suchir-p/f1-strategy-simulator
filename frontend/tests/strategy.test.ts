import { describe, expect, it } from "vitest";
import {
  addStop,
  formatStrategy,
  moveStop,
  parseStrategy,
  removeStop,
  setStintCompound,
  toStints,
  validateStrategy,
} from "../lib/strategy";

describe("strategy text", () => {
  it("round-trips the engine format", () => {
    for (const s of ["M-24-H", "S-12-M-35-H", "H-8-M-32-M-45-S"]) {
      expect(formatStrategy(parseStrategy(s)!)).toBe(s);
    }
    expect(parseStrategy("m-24-h")).toEqual({ start: "M", stops: [{ lap: 24, compound: "H" }] });
    expect(parseStrategy("M-24")).toBeNull();
    expect(parseStrategy("X-1-H")).toBeNull();
  });
});

describe("stints", () => {
  it("cover every lap exactly once", () => {
    const stints = toStints(parseStrategy("S-12-M-35-H")!, 53);
    expect(stints.map((s) => [s.compound, s.startLap, s.endLap])).toEqual([
      ["S", 1, 12],
      ["M", 13, 35],
      ["H", 36, 53],
    ]);
  });
});

describe("validation mirrors the engine rules", () => {
  const all = ["S", "M", "H"] as const;
  it("accepts a normal one-stop", () => {
    expect(validateStrategy(parseStrategy("M-24-H")!, 53, [...all])).toBeNull();
  });
  it("needs two compounds", () => {
    expect(validateStrategy(parseStrategy("M-24-M")!, 53, [...all])).toMatch(/two different/);
  });
  it("rejects a stop on or after the last lap", () => {
    expect(validateStrategy(parseStrategy("M-53-H")!, 53, [...all])).toMatch(/before lap 53/);
  });
  it("rejects compounds not brought to the race", () => {
    expect(validateStrategy(parseStrategy("S-20-M")!, 57, ["S", "H"])).toMatch(/Medium/);
  });
});

describe("editing", () => {
  const base = parseStrategy("M-24-H")!;
  it("keeps moved stops inside their neighbours", () => {
    expect(moveStop(base, 0, 60, 53).stops[0].lap).toBe(50);
    expect(moveStop(base, 0, -5, 53).stops[0].lap).toBe(3);
    const two = parseStrategy("S-12-M-35-H")!;
    expect(moveStop(two, 1, 13, 53).stops[1].lap).toBe(15);
  });
  it("adds a stop in the longest stint on a different compound", () => {
    const s = addStop(base, 53, ["S", "M", "H"]);
    expect(s.stops).toHaveLength(2);
    expect(validateStrategy(s, 53, ["S", "M", "H"])).toBeNull();
    expect(formatStrategy(s)).toBe("M-24-H-39-S");
  });
  it("changes and removes stints", () => {
    expect(formatStrategy(setStintCompound(base, 0, "S"))).toBe("S-24-H");
    expect(formatStrategy(setStintCompound(base, 1, "M"))).toBe("M-24-M");
    expect(formatStrategy(removeStop(parseStrategy("S-12-M-35-H")!, 0))).toBe("S-35-H");
  });
});

import {
  comparedSentence,
  formatLapRuns,
  lapsFromParam,
  lapsToParam,
  noiseHalfWidth,
  pitWindow,
  tireEffect,
} from "../lib/analysis";

describe("pit window", () => {
  const land: [string, number, number][] = [
    ["M-24-H", 4.1, 0.94],
    ["M-20-H", 4.3, 0.94],
    ["H-24-M", 4.2, 0.94],
    ["M-15-H-35-S", 5.0, 0.94],
    ["H-15-M-35-S", 4.8, 0.94],
    ["M-15-H-40-S", 5.2, 0.94],
  ];
  it("groups one-stops by tire pair, sorted by lap", () => {
    const w = pitWindow(land);
    expect(w.oneStop.map((l) => l.pair)).toEqual(["M>H", "H>M"]);
    expect(w.oneStop[0].points.map((p) => p.lap)).toEqual([20, 24]);
    expect(w.oneStop[0].label).toBe("Medium → hard");
    expect(w.best).toEqual({ strategy: "M-24-H", mean: 4.1 });
  });
  it("keeps the best two-stop per pair of laps", () => {
    const w = pitWindow(land);
    expect(w.twoStop).toHaveLength(2);
    expect(w.twoStop.find((c) => c.lap2 === 35)?.strategy).toBe("H-15-M-35-S");
  });
  it("estimates the noise level from interval widths", () => {
    expect(noiseHalfWidth([[0, 0], [0.1, 0.3], [0.2, 0.6], [0, 0.2]])).toBeCloseTo(0.1);
    expect(noiseHalfWidth([])).toBeNull();
  });
});

describe("tires", () => {
  const soft = { designation: "soft", pace_offset: 0, deg_linear: 0.1, deg_quadratic: 0.002 } as never;
  it("matches the engine's wear model", () => {
    expect(tireEffect(soft, 10)).toBeCloseTo(1.2);
  });
});

describe("safety-car what-if", () => {
  it("round-trips lap ranges through the URL", () => {
    expect(lapsFromParam("30-32")).toEqual([30, 31, 32]);
    expect(lapsToParam([30, 31, 32])).toBe("30-32");
    expect(lapsFromParam("32-30")).toEqual([]);
    expect(lapsFromParam("nonsense")).toEqual([]);
    expect(lapsFromParam(null)).toEqual([]);
  });
  it("formats runs of laps", () => {
    expect(formatLapRuns([5, 3, 4, 9])).toBe("3–5, 9");
  });
});

describe("compared sentence", () => {
  it("names better, worse or no clear difference", () => {
    expect(comparedSentence("the reference strategy (H-23-M)", 0.1, [0.02, 0.18])).toBe(
      "Compared with the reference strategy (H-23-M): 0.10 places worse on average (95% interval +0.02 to +0.18).",
    );
    expect(comparedSentence("X", -0.3, [-0.4, -0.2])).toMatch(/0.30 places better/);
    expect(comparedSentence("X", 0.02, [-0.05, 0.09])).toMatch(/no clear difference/);
  });
});

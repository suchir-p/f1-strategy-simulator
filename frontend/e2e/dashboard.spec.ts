import { expect, test } from "@playwright/test";

test("pre-race: edit a strategy, simulate it, and rank it against the search", async ({ page }) => {
  await page.goto("/?race=2024-italy&driver=LEC");
  await expect(page.getByText("Pre-race recommendation").first()).toBeVisible();
  const code = page.getByLabel("Strategy code");
  await expect(code).toHaveValue(/^[SMH]-\d+-[SMH]/);

  // Keyboard on the pit marker moves the stop and updates the code.
  const marker = page.getByRole("slider", { name: "Pit stop 1" });
  const before = Number(await marker.getAttribute("aria-valuenow"));
  await marker.focus();
  await page.keyboard.press("ArrowRight");
  await expect(marker).toHaveAttribute("aria-valuenow", String(before + 1));
  await expect(code).toHaveValue(new RegExp(`-${before + 1}-`));

  // Dragging the marker line itself (not just its tag) moves the stop.
  const line = page.locator(".pit-line").first();
  const box = (await line.boundingBox())!;
  const track = (await page.locator(".timeline-track").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 20);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + (track.width / 53) * 6, box.y + 20, { steps: 8 });
  await page.mouse.up();
  await expect(marker).toHaveAttribute("aria-valuenow", String(before + 7));

  // Invalid strategies are blocked before they reach the API.
  await code.fill("M-20-M");
  await expect(page.getByText("Use at least two different compounds")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Simulate/ })).toBeDisabled();
  await code.fill("M-20-H");

  await page.getByRole("button", { name: /^Simulate/ }).click();
  await expect(page.locator(".hero-label")).toHaveText("Typical finish", { timeout: 60_000 });
  await expect(page.getByText(/LEC finishes \d+% of races with M-20-H/)).toBeVisible();
  await expect(page.locator(".compare-verdict")).toContainText("Compared with the reference strategy");
  await expect(page.locator(".meta").first()).toContainText("Seed");
  await expect(page.locator(".stale-note")).toHaveCount(0);

  // Editing after a run flags the old numbers instead of leaving them unexplained.
  await code.fill("M-30-H");
  await expect(page.getByText("These results are for M-20-H. Simulate again to see M-30-H.")).toBeVisible();
  await code.fill("M-20-H");
  await expect(page.locator(".stale-note")).toHaveCount(0);

  await page.getByText("Run settings").click();
  await page.getByLabel("Search pit laps").selectOption("coarse");
  await page.getByRole("button", { name: "Find the best strategy" }).click();
  const table = page.locator("table.ranked");
  await expect(table).toBeVisible({ timeout: 180_000 });
  await expect(table.locator("tbody tr").first()).toContainText("Best");
  await expect(table.getByText("Yours")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pit window" })).toBeVisible();
  await page.getByRole("button", { name: "Two stops" }).click();
  await expect(page.getByRole("grid", { name: /Best average position/ })).toBeVisible();

  // Loading a ranked strategy puts it in the editor.
  const second = await table.locator("tbody tr").nth(1).locator(".code").innerText();
  await table.locator("tbody tr").nth(1).getByRole("button", { name: "Edit" }).click();
  await expect(code).toHaveValue(second);
  await expect(page.getByText(/“Yours” is the strategy you had when you searched \(M-20-H\)/)).toBeVisible();
});

test("early-season races only offer retrospective analysis, with the real result", async ({ page }) => {
  await page.goto("/?race=2024-bahrain&driver=VER&mode=pre_race");
  await expect(page.getByRole("button", { name: "Pre-race" })).toBeDisabled();
  await expect(page.getByText("Retrospective analysis").first()).toBeVisible();
  await expect(page.getByText("No pre-race data this early in the season")).toBeVisible();
  await expect(page.getByText(/Actually ran S-17-H-37-S and finished P1/)).toBeVisible();
});

test("pre-race view never shows how the race ended", async ({ page }) => {
  await page.goto("/?race=2024-italy&driver=LEC");
  await expect(page.getByText("Starting point: the reference strategy")).toBeVisible();
  await expect(page.getByText(/Actually ran/)).toHaveCount(0);
  await expect(page.locator(".tower .actual")).toHaveCount(0);
});

test("compare against the reference or your previous run, and replay one race", async ({ page }) => {
  await page.goto("/?race=2024-italy&driver=NOR&s=M-26-H");
  const code = page.getByLabel("Strategy code");
  // A shared link opens on its strategy, not the driver's default.
  await expect(code).toHaveValue("M-26-H");
  await expect(page.getByText(/Tire estimates have limited data/)).toBeVisible();

  await page.getByRole("button", { name: "Simulate and compare" }).click();
  const verdict = page.locator(".compare-verdict");
  await expect(verdict).toContainText("Compared with the reference strategy (H-23-M)", { timeout: 60_000 });
  await page.getByText("Side-by-side numbers").click();
  await expect(page.locator(".compare-table")).toContainText("Average position, scored");

  await code.fill("M-20-H");
  await page.getByRole("button", { name: "Simulate and compare" }).click();
  await expect(page.getByText(/NOR finishes \d+% of races with M-20-H/)).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Compared with").selectOption({ label: "Your previous run (M-26-H)" });
  await expect(page.getByText("Simulate again to compare with M-26-H.")).toBeVisible();
  await page.getByRole("button", { name: "Simulate and compare" }).click();
  await expect(verdict).toContainText("Compared with your previous run (M-26-H)", { timeout: 60_000 });

  // The race replay lives under Advanced analysis and is labeled as one example.
  await page.getByRole("tab", { name: "Race replay" }).click();
  await expect(page.getByText(/one of 1,000 possible outcomes with M-20-H/)).toBeVisible();
  await expect(page.getByText(/^NOR starts P1, pits on lap 20 for hards/)).toBeVisible({ timeout: 30_000 });
  // Editing the strategy replays the same race.
  await code.fill("M-24-H");
  await expect(page.getByText(/^NOR starts P1, pits on lap 24 for hards/)).toBeVisible();
});

test("a safety-car what-if shows on the timeline, the replay and the link", async ({ page }) => {
  await page.goto("/?race=2024-italy&driver=LEC");
  await expect(page.getByLabel("Strategy code")).toHaveValue(/^[SMH]-/);
  await page.getByText("Run settings").click();
  await page.getByLabel("Safety car from lap").selectOption("30");
  await expect(page.locator(".whatif")).toContainText("Safety car on laps 30–32");
  await expect(page.locator(".sc-band")).toBeVisible();
  await expect(page).toHaveURL(/sc=30-32/);
  await page.getByRole("tab", { name: "Race replay" }).click();
  await expect(page.locator(".trace-summary")).toContainText("Safety car on laps 30–32", { timeout: 30_000 });
  await page.locator(".whatif").getByRole("button", { name: "Clear" }).click();
  await expect(page.locator(".sc-band")).toHaveCount(0);
});

test("the header summary opens the full accuracy results", async ({ page }) => {
  await page.goto("/?race=2024-italy&driver=LEC");
  await expect(page.getByRole("tabpanel")).toHaveCount(0);
  await page.getByRole("link", { name: /Model accuracy: 0.87 rank correlation/ }).click();
  const panel = page.getByRole("tabpanel");
  await expect(panel).toBeInViewport();
  await expect(panel).toContainText("0.87");
  await expect(panel).toContainText("94%");
});

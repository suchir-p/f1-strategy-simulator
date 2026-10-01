import { defineConfig } from "@playwright/test";

// End-to-end tests drive the real stack: Next.js (started here) talking to a
// running FastAPI backend at F1SIM_API_URL (default http://127.0.0.1:8000).
export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  use: { baseURL: "http://localhost:3100", trace: "retain-on-failure" },
  webServer: {
    command: "npx next start -p 3100",
    url: "http://localhost:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});

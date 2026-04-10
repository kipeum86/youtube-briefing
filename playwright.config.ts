import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E test configuration.
 *
 * Uses `astro preview` as the dev server so tests run against the built
 * static site (not the dev server). This catches SSG-specific issues that
 * wouldn't surface in dev mode.
 *
 * Base URL includes the /youtube-briefing/ path prefix to match the
 * production Pages deployment. Tests navigate with relative paths so they
 * work both locally and in CI.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  timeout: 30_000,

  use: {
    // Base URL is the server root; tests navigate with the /youtube-briefing/
    // prefix explicitly so they mirror what a real visitor would type.
    baseURL: "http://localhost:4321",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: "npm run preview -- --port 4321 --host 127.0.0.1",
    url: "http://localhost:4321/youtube-briefing/",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});

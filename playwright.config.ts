import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E test configuration.
 *
 * Flow:
 *   1. globalSetup copies tests/e2e/fixtures/*.json into data/briefings/
 *   2. webServer command runs `astro build` (which picks up the fixtures via
 *      the astro:content glob loader) THEN `astro preview`
 *   3. All specs run against the built + fixture-populated site
 *   4. globalTeardown removes the fixtures from data/briefings/
 *
 * This lets the production repo stay in a genuine fresh-clone empty state
 * while E2E tests still have predictable populated data to assert against.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  testIgnore: ["**/fixtures/**", "**/global-setup.ts", "**/global-teardown.ts"],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "github" : "list",
  timeout: 30_000,

  // Copy fixture briefings into data/briefings/ before the Astro build runs,
  // then clean up. See tests/e2e/global-setup.ts for rationale.
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/global-teardown.ts",

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
    // Only run `astro preview` here — the build is done in global-setup.ts
    // so that fixtures are in place before astro reads the content collection.
    command: "npm run preview -- --port 4321 --host 127.0.0.1",
    url: "http://localhost:4321/youtube-briefing/",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});

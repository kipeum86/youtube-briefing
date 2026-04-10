/**
 * Global teardown for Playwright E2E tests.
 *
 * Removes the fixture briefings that global-setup.ts copied into
 * data/briefings/. Leaves .gitkeep untouched so the empty directory
 * survives subsequent builds.
 *
 * Runs ONCE after all specs finish.
 */

import { readdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const FIXTURES_DIR = join(REPO_ROOT, "tests", "e2e", "fixtures");
const TARGET_DIR = join(REPO_ROOT, "data", "briefings");

async function globalTeardown() {
  console.log("[e2e teardown] Removing fixture briefings from data/briefings/ ...");

  const fixtureFiles = (await readdir(FIXTURES_DIR)).filter((f) => f.endsWith(".json"));

  for (const filename of fixtureFiles) {
    try {
      await rm(join(TARGET_DIR, filename), { force: true });
    } catch (e) {
      // Ignore — fixture might not have been copied (e.g. setup failed)
    }
  }

  console.log(`[e2e teardown] Removed ${fixtureFiles.length} fixture briefings.`);
}

export default globalTeardown;

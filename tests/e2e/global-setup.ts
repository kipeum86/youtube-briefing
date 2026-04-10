/**
 * Global setup for Playwright E2E tests.
 *
 * Playwright runs globalSetup AFTER the webServer starts (despite docs
 * suggesting otherwise). To ensure the Astro build sees our fixtures, we:
 *
 *   1. Copy fixture briefings into data/briefings/
 *   2. Explicitly run `npm run build` here in setup so the dist/ is
 *      up-to-date with the fixtures BEFORE the webServer preview starts
 *      serving it.
 *
 * The webServer command in playwright.config.ts is just `npm run preview`,
 * no build, so the order is: setup copies → setup builds → webServer previews.
 *
 * The cleanup happens in global-teardown.ts.
 *
 * Why fixtures instead of committed seed data: the production data/briefings/
 * is intentionally empty (a real fresh-clone for forkers). E2E tests need
 * populated state to verify user flows, so we inject fixtures at test time
 * only.
 */

import { cp, readdir, mkdir, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const FIXTURES_DIR = join(REPO_ROOT, "tests", "e2e", "fixtures");
const TARGET_DIR = join(REPO_ROOT, "data", "briefings");

function run(cmd: string, args: string[], cwd: string): Promise<void> {
  return new Promise((ok, fail) => {
    const child = spawn(cmd, args, { cwd, stdio: "inherit", shell: false });
    child.on("close", (code) => {
      if (code === 0) ok();
      else fail(new Error(`${cmd} ${args.join(" ")} exited ${code}`));
    });
  });
}

async function globalSetup() {
  console.log("[e2e setup] Copying fixtures into data/briefings/ ...");
  await mkdir(TARGET_DIR, { recursive: true });

  const entries = await readdir(FIXTURES_DIR);
  const jsonFixtures = entries.filter((f) => f.endsWith(".json"));

  for (const filename of jsonFixtures) {
    await cp(join(FIXTURES_DIR, filename), join(TARGET_DIR, filename));
  }
  console.log(`[e2e setup] Copied ${jsonFixtures.length} fixture briefings.`);

  // Purge Astro content collection cache so the build picks up the
  // new fixtures immediately (Astro caches glob results in node_modules/.astro/)
  await rm(join(REPO_ROOT, ".astro"), { recursive: true, force: true });
  await rm(join(REPO_ROOT, "node_modules", ".astro"), { recursive: true, force: true });
  await rm(join(REPO_ROOT, "dist"), { recursive: true, force: true });

  console.log("[e2e setup] Running astro build with fixtures...");
  await run("npm", ["run", "build"], REPO_ROOT);
  console.log("[e2e setup] Build complete. Fixtures are in the dist/ output.");
}

export default globalSetup;

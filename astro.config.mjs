// @ts-check
import { defineConfig } from "astro/config";

// https://astro.build/config
export default defineConfig({
  site: "https://kipeum86.github.io",
  base: "/youtube-briefing",
  trailingSlash: "ignore",
  build: {
    format: "directory",
  },
});

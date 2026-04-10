// @ts-check
import { defineConfig } from "astro/config";

// https://astro.build/config
export default defineConfig({
  site: "https://kpsfamily.github.io",
  base: "/youtube-briefing",
  trailingSlash: "ignore",
  build: {
    format: "directory",
  },
});

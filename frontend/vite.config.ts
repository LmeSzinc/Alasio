import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { i18nPlugin } from "./scripts/i18n/vite.ts";
import { svelteDropDevPage } from "./scripts/svelte-drop-dev-page/vite.ts";

export default defineConfig({
  // i18nPlugin must come first: its config hook scans source files before
  // svelteDropDevPage renames dev route files for the build
  // svelteDropDevPage must be placed before sveltekit so it sees the raw svelte source
  plugins: [i18nPlugin(), svelteDropDevPage(), tailwindcss(), sveltekit()],
  server: {
    // Use 127.0.0.1
    host: "127.0.0.1",
    // port: 5173,
    proxy: {
      // redirect to backend
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  esbuild: {
    legalComments: "none",
  },
  build: {
    // The webui is served by the python backend and embedded in the
    // electron 22 shell (Chromium 108) via iframe; do not follow vite 8's
    // default baseline-widely-available (2026-01) target
    target: "chrome108",
  },
});

import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { i18nPlugin } from "./scripts/i18n/vite";
import { svelteDropDevPage } from "./scripts/svelte-drop-dev-page/vite";

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
});

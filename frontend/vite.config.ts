import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { i18nPlugin } from "./scripts/i18n/vite";

export default defineConfig({
  plugins: [i18nPlugin(), tailwindcss(), sveltekit()],
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

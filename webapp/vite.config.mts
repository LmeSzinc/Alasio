import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import electron from 'vite-plugin-electron';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import { i18nPlugin } from './scripts/i18n/vite';
import { mainI18nConfig, rendererI18nConfig } from './scripts/i18n/config';

const __dirname = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  // i18nPlugin (renderer instance) is listed first so its config hook
  // scans renderer sources before the sveltekit build starts
  plugins: [
    i18nPlugin(rendererI18nConfig),
    sveltekit(),
    tailwindcss(),
    electron([
      {
        entry: resolve(__dirname, 'main/index.ts'),
        vite: {
          // The main process runs as plain node (no HMR): the node-mode
          // i18n plugin rescans main sources on every buildStart, so
          // tray translations stay in sync during watch builds.
          plugins: [i18nPlugin(mainI18nConfig)],
          build: {
            // electron 22 bundles Node 16.17; do not follow vite 8's
            // default baseline-widely-available (2026-01) target
            target: 'node16',
            outDir: 'dist/main',
          },
        },
      },
      {
        entry: resolve(__dirname, 'preload/index.ts'),
        vite: {
          build: {
            // electron 22 bundles Node 16.17; do not follow vite 8's
            // default baseline-widely-available (2026-01) target
            target: 'node16',
            outDir: 'dist/preload',
          },
        },
      },
    ]),
  ],
  esbuild: {
    legalComments: "none",
  },
  build: {
    // electron 22 bundles Chromium 108; do not follow vite 8's default
    // baseline-widely-available (2026-01) target
    target: 'chrome108',
  },
});

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
            outDir: 'dist/main',
          },
        },
      },
      {
        entry: resolve(__dirname, 'preload/index.ts'),
        vite: {
          build: {
            outDir: 'dist/preload',
          },
        },
      },
    ]),
  ],
  esbuild: {
    legalComments: "none",
  },
});

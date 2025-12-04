import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';
import electron from 'vite-plugin-electron';
import renderer from 'vite-plugin-electron-renderer';
import { resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  plugins: [
    svelte(),
    tailwindcss(),
    electron([
      {
        entry: resolve(__dirname, 'main/index.ts'),
        vite: {
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
    renderer(),
  ],
  resolve: {
    alias: {
      '$lib': resolve(__dirname, 'renderer/src/lib'),
    },
  },
  root: 'renderer',
  build: {
    outDir: '../dist/renderer',
    emptyOutDir: true,
  },
  esbuild: {
    legalComments: "none",
  },
});

import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import electron from 'vite-plugin-electron';
import { resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  plugins: [
    sveltekit(),
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
  ],
  esbuild: {
    legalComments: "none",
  },
});

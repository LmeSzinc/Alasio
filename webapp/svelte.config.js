import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),

  kit: {
    // SPA mode: no SSR, single fallback page for all routes
    adapter: adapter({
      pages: 'dist/renderer',
      assets: 'dist/renderer',
      fallback: 'index.html',
    }),

    // Keep the existing electron project layout under renderer/
    files: {
      lib: 'renderer/src/lib',
      routes: 'renderer/src/routes',
      appTemplate: 'renderer/src/app.html',
    },
  },
};

export default config;

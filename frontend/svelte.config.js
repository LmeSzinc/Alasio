import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/**
 * @type {import('@sveltejs/kit').Config}
 * This is the configuration file for your SvelteKit project.
 * You can configure preprocessors, adapters, path aliases, and more here.
 * For a full list of options, see the official documentation:
 * https://kit.svelte.dev/docs/configuration
 */
const config = {
	/**
	 * Preprocessors
	 * --------------------------
	 * Preprocessors allow you to transform your code before it's passed to the
	 * Svelte compiler. `vitePreprocess` is a convenient utility that leverages
	 * Vite to handle TypeScript, PostCSS, SCSS, Less, etc., without extra setup.
	 * This lets you use `lang="ts"` in <script> tags or `lang="scss"` in <style> tags.
	 */
	preprocess: vitePreprocess(),

	/**
	 * Kit Configuration
	 * ---------------------------------------
	 * This is the core configuration section for all SvelteKit-specific features.
	 */
	kit: {
		/**
		 * Adapter
		 * --------------------
		 * The adapter determines the output of `npm run build`. It "adapts" your app
		 * for a specific deployment environment.
		 * `@sveltejs/adapter-static` is designed to output a set of files that can be
		 * hosted on any static web server.
		 */
		adapter: adapter({
			// `pages`: The directory to write the prerendered HTML pages to.
			// Default: 'build'
			pages: 'build',

			// `assets`: The directory to write the static assets (JS, CSS, images) to.
			// This is usually the same as the `pages` directory.
			// Default: 'build'
			assets: 'build',

			/**
			 * `fallback`: This is a key option for handling 404 pages in SSG mode.
			 * We set it to '404.html'. When SvelteKit builds, it will automatically
			 * compile your `src/routes/+error.svelte` file into `build/404.html`.
			 * This config tells the static host (like Vercel, Netlify): "If you can't
			 * find a file for the requested path, serve `404.html` instead."
			 * This allows the SvelteKit client-side router to take over and display
			 * the correct error page (global or route-specific), providing a much
			 * richer experience than a traditional static 404 page.
			 */
			fallback: '404.html',

			// `precompress`: If `true`, SvelteKit will create Brotli (.br) and Gzip (.gz)
			// compressed versions of your assets alongside the original files. If your
			// hosting platform supports serving these (most modern platforms do),
			// enabling this can significantly improve load times by reducing transfer sizes.
			precompress: false,

			// `strict`: If `true` (the default), the build will fail if the adapter
			// encounters a SvelteKit feature it doesn't support (e.g., API routes or
			// server-side form actions for `adapter-static`). This is a safety measure
			// to prevent accidentally deploying incompatible code.
			strict: true
		}),

		/**
		 * Path Aliases
		 * -----------------------
		 * Defining path aliases makes your import statements cleaner and easier to maintain.
		 * '$lib' is a default alias in SvelteKit that points to the `src/lib` directory.
		 * You can add your own custom aliases here.
		 */
		alias: {
			// Example: create an alias for a components directory
			// '$components': 'src/lib/components',
			// '$utils': 'src/lib/utils'
		},

		/**
		 * Prerendering
		 * ----------------------
		 * For SSG, you need to tell SvelteKit which pages to render into HTML at build time.
		 * With `adapter-static`, prerendering is enabled by default.
		 * You can control it precisely with the `entries` option.
		 */
		prerender: {
			// `entries`: An array of paths to prerender.
			// Using `['*']` is a wildcard that tells SvelteKit to discover and prerender
			// all pages reachable from the root page via `<a>` tags.
			// This is the most convenient setting for most SSG sites.
			// If you have pages that are not discoverable by the crawler (e.g., dynamic
			// routes from an external API), you need to add them manually here,
			// e.g., `['*', '/my-hidden-page']`.
			entries: ['*']

			// By default, the build will fail if an HTTP error (like a 404) occurs
			// during prerendering. You can adjust this behavior if needed.
			// onError: 'fail' // Can be 'continue', 'warn', or 'fail'
		}
	}
};

export default config;
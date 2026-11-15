import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import { inlineScriptHashes } from "./scripts/csp/inline-scripts.js";

// The page template carries one inline script: the pre-paint theme applied to
// embedded sessions. SvelteKit hashes the inline scripts it renders itself,
// but it never parses the template, so the hashes of the template scripts are
// declared here. They are recomputed on every config load (dev restart,
// build) from the template that is about to be compiled, so the two can not
// drift apart. `scripts/csp/verify.js` re-checks the built page.
// `import.meta.url` is a file url when the config is loaded by kit (it loads
// the config through vite). Tools that transform the config instead of
// importing it (vitest, in the tests of the directives below) get a virtual
// url, and `process.cwd()` is the only thing that locates the source tree.
const templateUrl = new URL("./src/app.html", import.meta.url);
const appTemplate =
  templateUrl.protocol === "file:" ? fileURLToPath(templateUrl) : resolve(process.cwd(), "src/app.html");
const appTemplateHashes = inlineScriptHashes(readFileSync(appTemplate, "utf8"));

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
     * Content Security Policy
     * ---------------------------------------
     * The policy is written by SvelteKit into the page (a meta tag, the page
     * is prerendered by the static adapter) and in dev into the response
     * header. The backend mirrors the meta into the production response
     * header, so both are enforced.
     *
     * Hash mode is the only usable mode here. A nonce can not be delivered to
     * the template script: `%sveltekit.nonce%` makes the adapter-static
     * fallback prerender throw, and without it the inline theme script would
     * be blocked in dev, where the inline scripts are served as they are.
     *
     * `frame-ancestors` is deliberately absent: a meta tag ignores it, and the
     * backend adds it to the response header (it allows the electron host).
     */
    csp: {
      mode: "hash",
      directives: {
        "default-src": ["self"],
        "script-src": ["self", ...appTemplateHashes],
        "style-src": ["self", "unsafe-inline"],
        "img-src": ["self", "data:", "blob:"],
        "connect-src": ["self", "ws:", "wss:"],
        "font-src": ["self", "data:"],
        "object-src": ["none"],
        "base-uri": ["self"],
        "form-action": ["self"],
        "frame-src": ["self"],
      },
    },

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
      pages: "build",

      // `assets`: The directory to write the static assets (JS, CSS, images) to.
      // This is usually the same as the `pages` directory.
      // Default: 'build'
      assets: "build",

      /**
       * `fallback`: Fallback to index.html for SPA
       */
      fallback: "index.html",

      // `precompress`: If `true`, SvelteKit will create Brotli (.br) and Gzip (.gz)
      // compressed versions of your assets alongside the original files. If your
      // hosting platform supports serving these (most modern platforms do),
      // enabling this can significantly improve load times by reducing transfer sizes.
      precompress: false,

      // `strict`: If `true` (the default), the build will fail if the adapter
      // encounters a SvelteKit feature it doesn't support (e.g., API routes or
      // server-side form actions for `adapter-static`). This is a safety measure
      // to prevent accidentally deploying incompatible code.
      strict: true,
    }),

    /**
     * Path Aliases
     * -----------------------
     * Defining path aliases makes your import statements cleaner and easier to maintain.
     * '$lib' is a default alias in SvelteKit that points to the `src/lib` directory.
     * You can add your own custom aliases here.
     */
    alias: {
      $src: "src",
      $routes: "src/routes",
      $public: "src/routes/(public)",
      $private: "src/routes/(private)",
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
      entries: ["*"],

      // By default, the build will fail if an HTTP error (like a 404) occurs
      // during prerendering. You can adjust this behavior if needed.
      // onError: 'fail' // Can be 'continue', 'warn', or 'fail'
    },
  },
};

export default config;

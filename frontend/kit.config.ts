import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import adapter from "@sveltejs/adapter-static";
import type { Config } from "@sveltejs/kit/vite";
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
 * The SvelteKit options of the webui.
 *
 * SvelteKit 3 reads them from the `sveltekit()` call of `vite.config.ts` and
 * refuses a `svelte.config.js`; they live in this module so the CSP test can
 * assert the same object the build uses without loading the Vite config.
 * Options are top level (SvelteKit 2 nested them under `kit`).
 *
 * Path aliases are not part of this object: SvelteKit 3 deprecated its `alias`
 * option and dropped the implicit `$lib`, so each alias is declared where its
 * consumer reads it — `resolve.alias` in vite.config.ts (and vitest.config.ts
 * for the tests), `paths` in tsconfig.json. Keep the three lists in sync.
 */
const config: Config = {
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
   * The adapter determines the output of `vite build`. It "adapts" your app
   * for a specific deployment environment.
   * `@sveltejs/adapter-static` is designed to output a set of files that can be
   * hosted on any static web server.
   */
  adapter: adapter({
    // `pages`: The directory to write the prerendered HTML pages to.
    pages: "build",

    // `assets`: The directory to write the static assets (JS, CSS, images) to.
    // This is usually the same as the `pages` directory.
    assets: "build",

    /**
     * `fallback`: Fallback to index.html for SPA
     */
    fallback: "index.html",

    // `precompress`: Create Brotli (.br) and Gzip (.gz) versions of the assets
    // alongside the original files. The backend serves the files as they are,
    // so the adapters' copies would only double the output.
    precompress: false,

    // `strict`: fail the build on a SvelteKit feature the static output can
    // not serve (API routes, server-side form actions).
    strict: true,
  }),

  /**
   * Prerendering
   * ----------------------
   * For SSG, you need to tell SvelteKit which pages to render into HTML at build time.
   * With `adapter-static`, prerendering is enabled by default.
   * `entries: ['*']` has the crawler follow the links from the root page.
   */
  prerender: {
    entries: ["*"],
  },
};

export default config;

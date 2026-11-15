import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

/**
 * Vitest configuration for the frontend.
 *
 * Kept separate from vite.config.ts on purpose: the sveltekit plugin
 * (and the i18n / dev-page plugins) are not needed in tests, and their
 * behavior (virtual modules, route scanning) would only add noise.
 * The svelte plugin alone compiles the runes in `.svelte.ts` source
 * files; svelte.config.js is not loaded (configFile: false) so kit
 * preprocessing stays out of the test pipeline.
 *
 * `$app/*` are SvelteKit virtual modules; tests resolve them to small
 * stubs under src/test-utils so product code can be imported without
 * modification.
 */
export default defineConfig({
  plugins: [svelte({ configFile: false })],
  test: {
    environment: "jsdom",
    // scripts/ holds build-time helpers (the CSP hash check) that have their
    // own unit tests; they are plain TS/JS modules, not part of the app.
    include: ["src/**/*.test.ts", "scripts/**/*.test.ts"],
  },
  // Expose the shared message fixtures path to the contract test. The
  // fixtures live outside the frontend tree (tests/ws_fixtures/), and
  // import.meta.url in vitest workers is not a file:// url, so the path
  // is computed here in the config file and injected at build time.
  define: {
    "import.meta.env.WS_FIXTURES_PATH": JSON.stringify(
      fileURLToPath(new URL("../tests/ws_fixtures/messages.json", import.meta.url)),
    ),
  },
  resolve: {
    // Component tests mount components with svelte's client-side mount()
    // API; without the browser condition the "svelte" package resolves
    // to the server build ("mount is not available on the server").
    conditions: ["browser"],
    alias: {
      // Same aliases the svelte.config.js project config provides;
      // vitest loads this config standalone so they must be declared
      // here.
      $lib: fileURLToPath(new URL("./src/lib", import.meta.url)),
      $src: fileURLToPath(new URL("./src", import.meta.url)),
      $routes: fileURLToPath(new URL("./src/routes", import.meta.url)),
      $public: fileURLToPath(new URL("./src/routes/(public)", import.meta.url)),
      $private: fileURLToPath(new URL("./src/routes/(private)", import.meta.url)),
      "$app/environment": fileURLToPath(new URL("./src/lib/test-utils/app-environment.ts", import.meta.url)),
      "$app/navigation": fileURLToPath(new URL("./src/lib/test-utils/app-navigation.ts", import.meta.url)),
    },
  },
});

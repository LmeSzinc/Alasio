import { type Plugin } from "vite";
import { i18nConfig, resolvePath } from "./config.ts";
import { I18nGenerator } from "./core.ts";

// Singleton instance to maintain cache state during dev server runtime
export const generator = new I18nGenerator(i18nConfig);

// Process-level flag: the second (client) build pass re-bundles the vite
// config, which recreates this module and its plugin instance, so an
// in-module variable would be lost. The build source scan must run exactly
// once, before dev route files are renamed by the svelte-drop-dev-page
// plugin.
const BUILD_INIT_FLAG = "__ALASIO_I18N_BUILD_INITIALIZED__";

export function i18nPlugin(): Plugin {
  let command: "build" | "serve" = "serve";
  let devInitialized = false;

  return {
    name: "vite-plugin-svelte-i18n",
    // Must be an enforce: 'pre' plugin so its config hook runs before
    // svelte-drop-dev-page (also 'pre'), which renames dev route files
    // during build. The source scan therefore completes while every route
    // file is still in place.
    enforce: "pre",

    config: {
      // Runs before svelte-drop-dev-page's config hook (this plugin is
      // listed first in the plugins array), so the source scan completes
      // while every route file is still in place.
      order: "pre",
      async handler(_config, env) {
        command = env.command;
        // During build, scan source files before dev route files are
        // renamed by svelte-drop-dev-page, so the scan sees every i18n
        // usage. Stale-key cleanup only runs against a complete scan,
        // otherwise translations of temporarily hidden route files would
        // be dropped and re-created as defaults when the files come back.
        // Only runs once per process (the second, client build pass
        // re-bundles the config and would rescan with route files already
        // renamed).
        if (command === "build" && !process.env[BUILD_INIT_FLAG]) {
          await generator.init();
          process.env[BUILD_INIT_FLAG] = "1";
        }
      },
    },

    // Dev server: run the full scan here, after svelte-drop-dev-page's
    // config hook has restored any route files left dropped by an
    // interrupted build.
    async buildStart() {
      if (command !== "build" && !devInitialized) {
        await generator.init();
        devInitialized = true;
      }
    },

    // Handle HMR
    async handleHotUpdate({ file }) {
      const absSrc = resolvePath(i18nConfig.srcPath);
      const absGen = resolvePath(i18nConfig.genPath);
      const absI18n = resolvePath(i18nConfig.i18nPath);

      // 1. Translation JSON changed
      if (file.startsWith(absI18n) && file.endsWith(".json")) {
        await generator.handleJsonUpdate(file);
        // No reload on json changes since i18n json will be baked into ts file
        return [];
      }

      // 2. Source code changed (ignore generated files)
      if (
        (file.endsWith(".svelte") || file.endsWith(".ts") || file.endsWith(".js")) &&
        file.startsWith(absSrc) &&
        !file.startsWith(absGen)
      ) {
        await generator.handleSourceUpdate(file);
      }
    },
  };
}

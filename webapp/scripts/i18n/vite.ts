import { type Plugin } from "vite";
import { I18nGenerator } from "./core";
import { type I18nConfig, resolvePath } from "./config";

// Process-level flag: the second (client) build pass re-bundles the vite
// config, which recreates this module and its plugin instance, so an
// in-module variable would be lost. The build source scan must run exactly
// once, before dev route files are renamed by the svelte-drop-dev-page
// plugin. Only meaningful for the renderer (svelte mode) instance; the main
// (node mode) instance is built by vite-plugin-electron in watch mode with
// no HMR, so it rescans on every buildStart instead (see below).
const BUILD_INIT_FLAG = "__ALASIO_I18N_BUILD_INITIALIZED__";

export function i18nPlugin(config: I18nConfig): Plugin {
  const generator = new I18nGenerator(config);
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
        if (command === "build" && config.mode === "svelte" && !process.env[BUILD_INIT_FLAG]) {
          await generator.init();
          process.env[BUILD_INIT_FLAG] = "1";
        }
      },
    },

    // Dev server: run the full scan here, after svelte-drop-dev-page's
    // config hook has restored any route files left dropped by an
    // interrupted build.
    //
    // Node mode (electron main build): vite-plugin-electron rebuilds the
    // main bundle in watch mode with no HMR. The process-level flag would
    // suppress the scan after the first pass, so the node instance runs a
    // full init on every buildStart to pick up main-source changes. If the
    // JSON was not updated after editing main sources, run `pnpm i18ngen`
    // manually as a fallback.
    async buildStart() {
      if (command !== "build") {
        if (!devInitialized) {
          await generator.init();
          devInitialized = true;
        }
      } else if (config.mode === "node") {
        await generator.init();
      }
    },

    // Handle HMR
    async handleHotUpdate({ file }) {
      const absSrc = resolvePath(config, config.srcPath);
      const absGen = resolvePath(config, config.genPath);
      const absI18n = resolvePath(config, config.i18nPath);

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

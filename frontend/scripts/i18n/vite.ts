import path from "node:path";
import { type Plugin } from "vite";
import { type I18nConfig } from "./config.ts";
import { I18nGenerator } from "./core.ts";

// Process-level flag: the second (client) build pass re-bundles the vite
// config, which recreates this module and its plugin instances, so an
// in-module variable would be lost. The build source scan must run exactly
// once per process, before any plugin hides dev-only route files from the
// build (svelte-drop-dev-page in the frontend).
const BUILD_INIT_FLAG = "__ALASIO_I18N_BUILD_INITIALIZED__";

/**
 * Absolute path with forward slashes, matching the ids Vite reports to
 * `handleHotUpdate` on every platform.
 *
 * Args:
 *     config (I18nConfig): Config the path belongs to
 *     parts (str, optional): Path parts below the config cwd
 *
 * Returns:
 *     str: Absolute path with forward slashes
 */
function absolutePath(config: I18nConfig, ...parts: string[]): string {
  return path.resolve(config.cwd, ...parts).replace(/\\/g, "/");
}

/**
 * Vite plugin that keeps the generated i18n artifacts in sync with the
 * `t.Module.Key()` usages in the source files.
 *
 * All configs owned by the build must be listed: only the top-level vite
 * config is resolved by `svelte-kit sync`, so a config owned by a nested
 * build (the electron main process) is never generated during a sync
 * unless the top-level plugin lists it too.
 *
 * Node mode configs belong to a nested watch build without HMR: they are
 * rescanned on every `buildStart` and are not handled by `handleHotUpdate`.
 *
 * Args:
 *     configs (list[I18nConfig]): Configs to keep in sync
 *
 * Returns:
 *     Plugin: Vite plugin
 */
export function i18nPlugin(configs: I18nConfig[]): Plugin {
  const entries = configs.map((config) => ({ config, generator: new I18nGenerator(config) }));
  let command: "build" | "serve" = "serve";
  let devInitialized = false;

  return {
    name: "vite-plugin-svelte-i18n",
    // Must be an enforce: 'pre' plugin so its config hook runs before the
    // plugins that hide dev-only route files from the build.
    enforce: "pre",

    config: {
      order: "pre",
      async handler(_config, env) {
        command = env.command;
        // During a build, scan source files before any plugin renames
        // dev-only route files, so the scan sees every i18n usage.
        // Stale-key cleanup only runs against a complete scan, otherwise
        // translations of temporarily hidden route files would be dropped
        // and re-created as defaults when the files come back. Only runs
        // once per process (the second, client build pass re-bundles the
        // config).
        if (command === "build" && !process.env[BUILD_INIT_FLAG]) {
          for (const { generator } of entries) {
            await generator.init();
          }
          process.env[BUILD_INIT_FLAG] = "1";
        }
      },
    },

    async buildStart() {
      if (command !== "build") {
        // Dev server: run the full scan here, after the route-hiding
        // plugins have restored any route files left dropped by an
        // interrupted build. Node mode configs are owned by their own
        // nested watch build, which rescans them itself.
        if (devInitialized) return;
        for (const { config, generator } of entries) {
          if (config.mode === "svelte") await generator.init();
        }
        devInitialized = true;
        return;
      }
      // Node mode (electron main build): vite-plugin-electron rebuilds the
      // main bundle in watch mode without HMR, so the config is rescanned
      // on every build start. Svelte mode configs were generated in the
      // config hook and stay up to date through handleHotUpdate.
      for (const { config, generator } of entries) {
        if (config.mode === "node") await generator.init();
      }
    },

    async handleHotUpdate({ file }) {
      // Node mode configs have no HMR; their nested watch build rescans on
      // every buildStart.
      for (const { config, generator } of entries) {
        if (config.mode !== "svelte") continue;

        const absSrc = absolutePath(config, config.srcPath);
        const absGen = absolutePath(config, config.genPath);
        const absI18n = absolutePath(config, config.i18nPath);

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
      }
    },
  };
}

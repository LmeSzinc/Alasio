import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "fs";
import { join, resolve } from "path";
import { fileURLToPath } from "url";
import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { type Plugin, defineConfig } from "vite";
import electron from "vite-plugin-electron";
import { cspInlineHashDev } from "./scripts/csp-inline-hash/vite.ts";
import { mainI18nConfig, rendererI18nConfig } from "./scripts/i18n/config.ts";
import { i18nPlugin } from "./scripts/i18n/vite.ts";

const __dirname = fileURLToPath(new URL(".", import.meta.url));

/**
 * vite's mergeConfig replaces array values, so the `server.watch.ignored`
 * set by sveltekit's config hook would wipe out any ignored patterns
 * declared in defineConfig. This plugin runs after kit (kit is
 * enforce: 'pre', this one is not) and merges the patterns in, so the
 * dev watcher does not open handles on build output directories; on
 * Windows those handles block the next `pnpm build` from cleaning them.
 *
 * @param patterns Glob patterns to ignore, relative to the project root
 */
function ignoreBuildOutput(...patterns: string[]): Plugin {
  return {
    name: "svelte-kit-watch-ignore-build-output",
    config(config) {
      config.server ??= {};
      config.server.watch ??= {};
      // ignored may be a single matcher or an array; normalize to an array
      // before merging so sveltekit's patterns are preserved
      const { ignored } = config.server.watch;
      config.server.watch.ignored = [...(Array.isArray(ignored) ? ignored : ignored ? [ignored] : []), ...patterns];
    },
  };
}

/**
 * svelte-kit writes ESM output (e.g. .svelte-kit/output/server/*.js) into a
 * package without "type": "module", so node reparses those files and emits
 * MODULE_TYPELESS_PACKAGE_JSON warnings. Mark the generated .svelte-kit
 * directory as ESM (it is gitignored, so ensure the marker on every build).
 */
function ensureSvelteKitEsmMarker() {
  const dir = resolve(__dirname, ".svelte-kit");
  const pkg = resolve(dir, "package.json");
  if (existsSync(pkg)) {
    return;
  }
  mkdirSync(dir, { recursive: true });
  writeFileSync(pkg, '{\n  "type": "module"\n}\n');
}

/**
 * .svelte-kit/generated is watched by vite (kit only ignores the other
 * .svelte-kit siblings), and external processes rewrite it wholesale:
 * `pnpm run codegen` spawns `svelte-kit sync`, whose write-if-changed
 * cache is per-process, so it rewrites every generated file even when
 * nothing changed. Each identical rewrite bumps the mtime and is treated
 * by vite as a module change, cascading into HMR updates / full page
 * reloads in the running dev server.
 *
 * Snapshot the generated files at startup and ignore change events whose
 * content is identical to the snapshot; real changes (e.g. route
 * add/remove, which rewrites root.svelte and the client nodes) still go
 * through normal HMR.
 */
function svelteKitGeneratedHmrGuard(): Plugin {
  let snapshot: Map<string, string> | null = null;

  const snapshotDir = (dir: string): Map<string, string> => {
    const out = new Map<string, string>();
    const walk = (d: string) => {
      for (const entry of readdirSync(d, { withFileTypes: true })) {
        const full = join(d, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else {
          out.set(full, readFileSync(full, "utf-8"));
        }
      }
    };
    walk(dir);
    return out;
  };

  return {
    name: "svelte-kit-generated-hmr-guard",
    configResolved(config) {
      try {
        snapshot = snapshotDir(join(config.root, ".svelte-kit", "generated"));
      } catch {
        // Directory does not exist yet (fresh checkout); the first
        // rewrite is then treated as a real change, which is correct.
        snapshot = new Map();
      }
    },
    async hotUpdate({ type, file, read }) {
      if (type !== "update" || !snapshot) return;
      if (!file.replace(/\\/g, "/").includes("/.svelte-kit/generated/")) return;
      let content;
      try {
        content = await read();
      } catch {
        // Transient read failure (e.g. an atomic save in progress): do
        // not swallow the event.
        return;
      }
      const prev = snapshot.get(file);
      if (prev === content) {
        // Identical rewrite by an external process: not a real change.
        return [];
      }
      snapshot.set(file, content);
    },
  };
}

export default defineConfig({
  // i18nPlugin is listed first so its config hook scans sources before the
  // sveltekit build starts. It owns every i18n config of the project:
  // only this top-level config is resolved by `svelte-kit sync`, so the
  // main process config must be generated here as well (the nested main
  // build never runs during a sync).
  plugins: [
    i18nPlugin([rendererI18nConfig, mainI18nConfig]),
    // SvelteKit options are passed directly (supported since SvelteKit
    // 2.62.0) instead of a svelte.config.js file: webapp has no
    // "type": "module" (electron 22 needs a CJS main process), so an ESM
    // config file would emit a MODULE_TYPELESS_PACKAGE_JSON warning while
    // a CJS one cannot load adapter-static ("import"-only exports).
    sveltekit({
      preprocess: vitePreprocess(),
      // SPA mode: no SSR, single fallback page for all routes
      adapter: adapter({
        pages: "dist/renderer",
        assets: "dist/renderer",
        fallback: "index.html",
      }),

      // Keep the existing electron project layout under renderer/
      files: {
        lib: "renderer/lib",
        routes: "renderer/routes",
        appTemplate: "renderer/app.html",
      },

      // Path aliases are declared here instead of tsconfig.json "paths",
      // so svelte-kit sync generates them into .svelte-kit/tsconfig.json.
      // $lib is generated automatically from kit.files.lib.
      alias: {
        $src: "renderer",
        $routes: "renderer/routes",
      },
    }),
    tailwindcss(),
    electron([
      {
        entry: resolve(__dirname, "main/index.ts"),
        vite: {
          // The main process runs as plain node (no HMR): the node-mode
          // i18n plugin rescans main sources on every buildStart, so
          // tray translations stay in sync during watch builds.
          plugins: [i18nPlugin([mainI18nConfig])],
          build: {
            // electron 22 bundles Node 16.17; do not follow vite 8's
            // default baseline-widely-available (2026-01) target
            target: "node16",
            outDir: "dist/main",
          },
        },
      },
      {
        entry: resolve(__dirname, "preload/index.ts"),
        vite: {
          build: {
            // electron 22 bundles Node 16.17; do not follow vite 8's
            // default baseline-widely-available (2026-01) target
            target: "node16",
            outDir: "dist/preload",
          },
        },
      },
    ]),
    {
      name: "svelte-kit-esm-marker",
      configResolved() {
        ensureSvelteKitEsmMarker();
      },
    },
    svelteKitGeneratedHmrGuard(),
    ignoreBuildOutput("**/dist/**"),
    // dev: intercept html responses before sveltekit injects its
    // bootstrap script (enforce pre inside the plugin)
    cspInlineHashDev(),
  ],
  server: {
    fs: {
      // sveltekit 2.70 restricts fs.allow to its source dirs
      // (renderer/lib, renderer/routes, ...); the renderer root itself
      // (app.css, app.html) is served by vite in dev and must be allowed.
      allow: [__dirname],
    },
  },
  esbuild: {
    legalComments: "none",
  },
  build: {
    // electron 22 bundles Chromium 108; do not follow vite 8's default
    // baseline-widely-available (2026-01) target
    target: "chrome108",
  },
});

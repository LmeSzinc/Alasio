import { readFileSync, readdirSync } from "fs";
import { join } from "path";
import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { type Plugin, defineConfig } from "vite";
import { cspInlineHashDev } from "./scripts/csp-inline-hash/vite.ts";
import { i18nConfig } from "./scripts/i18n/config.ts";
import { i18nPlugin } from "./scripts/i18n/vite.ts";
import { svelteDropDevPage } from "./scripts/svelte-drop-dev-page/vite.ts";

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
  // i18nPlugin must come first: its config hook scans source files before
  // svelteDropDevPage renames dev route files for the build
  // svelteDropDevPage must be placed before sveltekit so it sees the raw svelte source
  plugins: [
    i18nPlugin([i18nConfig]),
    svelteDropDevPage(),
    tailwindcss(),
    sveltekit(),
    svelteKitGeneratedHmrGuard(),
    ignoreBuildOutput("**/build/**"),
    // dev: intercept html responses before sveltekit injects its
    // bootstrap script (enforce pre inside the plugin)
    cspInlineHashDev(),
  ],
  server: {
    // Use 127.0.0.1
    host: "127.0.0.1",
    // port: 5173,
    proxy: {
      // redirect to backend
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  esbuild: {
    legalComments: "none",
  },
  build: {
    // The webui is served by the python backend and embedded in the
    // electron 22 shell (Chromium 108) via iframe; do not follow vite 8's
    // default baseline-widely-available (2026-01) target
    target: "chrome108",
  },
});

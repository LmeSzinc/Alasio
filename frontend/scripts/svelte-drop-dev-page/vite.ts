import path from "node:path";
import { type Plugin } from "vite";
import { dropMarkedRoutes, restoreDroppedRoutes } from "./files.ts";

/**
 * Default marker comment.
 *
 * When a route file contains this comment, the whole route is dropped
 * from the production build while staying accessible in dev.
 *
 * Usage in a svelte file (inside a `<script>` block):
 *
 *     // !!![svelte-drop-dev-page]!!!
 */
export const DEFAULT_MARKER = "// !!![svelte-drop-dev-page]!!!";

/**
 * Default replacement content for marked non-route svelte files.
 *
 * A plain text node, which compiles to a trivial component without any
 * import. The marked file's own code and dependencies are therefore
 * dropped from the bundle.
 */
export const DEFAULT_REPLACEMENT = "svelte-drop-dev-page";

export interface SvelteDropDevPageOptions {
  /**
   * Custom marker comment.
   *
   * Defaults to '// !!![svelte-drop-dev-page]!!!'.
   */
  marker?: string;

  /**
   * Replacement content of marked non-route svelte files.
   *
   * Defaults to 'svelte-drop-dev-page'.
   */
  replacement?: string;

  /**
   * Directory containing sveltekit routes, relative to the vite root.
   *
   * Defaults to 'src/routes'.
   */
  routesDir?: string;
}

/**
 * Vite plugin to drop dev-only pages from the production build.
 *
 * Route files (e.g. `+page.svelte`) containing the marker comment are
 * renamed to a temporary dropped suffix (e.g. `+page.svelte.dropped`)
 * during build, before `svelte-kit sync` scans the filesystem. The
 * routes therefore do not exist in the route table at all, and nothing
 * (no node, no chunk, no placeholder) is emitted for them in the build
 * output.
 *
 * Interrupted build repair:
 * - Running `vite build` again continues the interrupted drop: files
 *   already dropped are kept, remaining marked files are dropped too.
 * - Starting `vite dev` restores every file left dropped by an
 *   interrupted build, so dev-only pages are accessible again.
 *
 * The plugin must be placed before the sveltekit plugin in the `plugins`
 * array (and runs with `enforce: 'pre'`), so its `configResolved` hook
 * runs before sveltekit's, which performs the route scan.
 *
 * @param options Plugin options
 * @returns Vite plugin
 */
export function svelteDropDevPage(options: SvelteDropDevPageOptions = {}): Plugin {
  const marker = options.marker ?? DEFAULT_MARKER;
  const replacement = options.replacement ?? DEFAULT_REPLACEMENT;
  const routesDir = options.routesDir ?? "src/routes";

  let command: "build" | "serve" = "serve";
  let ssr = false;
  let routesRoot = "";

  return {
    name: "svelte-drop-dev-page",
    // Run before the sveltekit plugins (which are enforce: 'pre') so the
    // route files are dropped before sveltekit's config hook route scan
    // (sync.all), and restored before the dev server syncs routes.
    enforce: "pre",

    config: {
      // sveltekit's own config hook is order: 'pre' and performs the route
      // scan there; this hook must run before it
      order: "pre",
      handler(config, env) {
        const root = path.resolve(config.root ?? process.cwd(), routesDir);
        if (env.command === "build") {
          // Drop marked route files so the route scan does not see them.
          // Files already dropped by an interrupted build are kept as-is,
          // and the remaining marked files are dropped to complete it.
          const dropped = dropMarkedRoutes(root, marker);
          if (dropped.length > 0) {
            this.info(`[svelte-drop-dev-page] dropped ${dropped.length} route file(s) for build`);
          }
        } else {
          // Dev server (or preview): restore files left dropped by an
          // interrupted build, so dev-only pages are accessible again.
          const restored = restoreDroppedRoutes(root);
          if (restored.length > 0) {
            this.info(`[svelte-drop-dev-page] restored ${restored.length} route file(s) from interrupted build`);
          }
        }
      },
    },

    configResolved(config) {
      command = config.command;
      // build.ssr is boolean | string; any non-false value means an SSR pass
      ssr = config.build.ssr !== false;
      routesRoot = path.resolve(config.root, routesDir);
    },

    closeBundle() {
      // Vite build runs two passes (client + ssr); restore the dropped
      // files only after the last one, when the build output is complete.
      if (command !== "build" || !ssr) return;
      const restored = restoreDroppedRoutes(routesRoot);
      if (restored.length > 0) {
        this.info(`[svelte-drop-dev-page] restored ${restored.length} route file(s) after build`);
      }
    },

    transform(code, id) {
      // Fallback for marked non-route svelte files (components): replace
      // the whole file with a plain text placeholder during build, so
      // their code and imports are tree-shaken out of the bundle.
      if (command !== "build") return;
      if (!id.endsWith(".svelte") || id.includes("/node_modules/")) {
        return;
      }
      if (!code.includes(marker)) {
        return;
      }
      this.info(`[svelte-drop-dev-page] drop marked file: ${id}`);
      return { code: replacement, map: null };
    },
  };
}

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { KitConfig } from "@sveltejs/kit";
import { inlineScriptHashes } from "./inline-scripts.ts";

/**
 * The Content-Security-Policy of the renderer, as SvelteKit's `kit.csp`.
 *
 * The policy is written by SvelteKit into the page (a meta tag: the static
 * adapter prerenders the fallback page) and in dev into the response header,
 * so the renderer is protected in both modes without a custom vite plugin.
 *
 * Hash mode is the only usable mode here. A nonce can not be delivered to the
 * template script: `%sveltekit.nonce%` makes the adapter-static fallback
 * prerender throw, and without it the inline theme script would be blocked in
 * dev, where the inline scripts are served as they are.
 *
 * SvelteKit hashes the inline scripts it renders itself, but it never parses
 * the app template, so the template hashes are read from renderer/app.html on
 * every config load (dev start, build, sync): the policy and the template can
 * not drift apart. `scripts/csp/verify.ts` re-checks the built page.
 */
export function appTemplatePath(): string {
  const candidates = [
    // scripts and the build run with the webapp root as the working directory
    resolve(process.cwd(), "renderer", "app.html"),
    // direct runs (tsx, vitest) can locate the file from this module. A config
    // bundled by vite has a temporary import.meta.url, that candidate then
    // simply does not exist
    fileURLToPath(new URL("../../renderer/app.html", import.meta.url)),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(`[csp] renderer/app.html not found, tried: ${candidates.join(", ")}`);
}

/**
 * Build the csp config of the renderer.
 *
 * @param templatePath Path of the app template. Defaults to appTemplatePath()
 * @returns The `kit.csp` value, every template inline script covered by hash
 */
export function rendererCsp(templatePath: string = appTemplatePath()): NonNullable<KitConfig["csp"]> {
  const html = readFileSync(templatePath, "utf8");
  return {
    mode: "hash",
    directives: {
      "default-src": ["self"],
      "script-src": ["self", ...inlineScriptHashes(html)],
      "style-src": ["self", "unsafe-inline"],
      "img-src": ["self", "data:", "blob:"],
      "connect-src": ["self", "ws:", "wss:"],
      "font-src": ["self", "data:"],
      "object-src": ["none"],
      "base-uri": ["self"],
      "form-action": ["self"],
      // the renderer embeds the frontend served by the backend, whose port is
      // configurable
      "frame-src": ["self", "http://127.0.0.1:*"],
    },
  };
}

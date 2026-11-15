import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import config from "../../svelte.config.js";
import { cspMetaContent, inlineScripts, scriptHashes } from "./inline-scripts.js";

/**
 * Wiring tests between the page template and the CSP SvelteKit writes.
 *
 * The policy is split in two: SvelteKit hashes the inline scripts it renders
 * itself, and svelte.config.js supplies the hashes of the scripts written in
 * app.html. Nothing forces the two sides to agree at build time (a missing
 * hash only shows up as a blocked script in the browser), so the agreement is
 * checked here, where a failing expectation names the cause.
 *
 * The built page is checked by scripts/csp/verify.js after `vite build`: that
 * is the same invariant over the actual output.
 */
// vitest runs with the project root as the working directory, and
// `import.meta.url` of a transformed module is not a file url.
const template = readFileSync(resolve(process.cwd(), "src/app.html"), "utf8");
const csp = config.kit?.csp;
const directives = csp?.directives ?? {};
const scriptSources = directives["script-src"] ?? [];
const templateScripts = inlineScripts(template);

describe("TestAppTemplateCsp", () => {
  it("has an inline script to hash", () => {
    // Guards the tests below: they are vacuous without a template script.
    expect(templateScripts.length).toBeGreaterThan(0);
  });

  it("lists every hash variant of every template inline script", () => {
    for (const body of templateScripts) {
      for (const hash of scriptHashes(body)) {
        expect(scriptSources).toContain(hash);
      }
    }
  });

  it("uses hash mode", () => {
    // A nonce can not be delivered to a template script: the placeholder makes
    // the adapter-static fallback prerender throw.
    expect(csp?.mode).toBe("hash");
  });

  it("does not mix unsafe-inline into script-src", () => {
    // A hash does not apply when 'unsafe-inline' is present: the browser
    // ignores the hashes and allows everything instead.
    expect(scriptSources).not.toContain("unsafe-inline");
  });

  it("keeps the template free of a CSP meta", () => {
    // SvelteKit writes the meta from kit.csp. A hand written one would be
    // enforced in addition to the generated one, and the hand written hashes
    // are stale by definition.
    expect(cspMetaContent(template)).toBe(null);
  });

  it.each([
    "default-src",
    "script-src",
    "style-src",
    "img-src",
    "connect-src",
    "font-src",
    "object-src",
    "base-uri",
    "form-action",
    "frame-src",
  ])("declares %s", (name) => {
    expect(directives).toHaveProperty(name);
  });

  it("leaves frame-ancestors to the response header", () => {
    // A meta tag ignores frame-ancestors, and the backend adds it to the
    // response header (it has to allow the electron host).
    expect(directives).not.toHaveProperty("frame-ancestors");
  });
});

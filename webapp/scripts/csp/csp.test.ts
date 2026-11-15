import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { appTemplatePath, rendererCsp } from "./config";
import { cspMetaContent, inlineScripts, scriptHashes, scriptSrcSources, verifyPage } from "./inline-scripts";

/**
 * Guards for the renderer Content-Security-Policy.
 *
 * The policy is split in two: SvelteKit hashes the inline scripts it renders
 * itself, and scripts/csp/config.ts supplies the hashes of the scripts written
 * in renderer/app.html. Nothing forces the two sides to agree at build time (a
 * missing hash only shows up as a blocked script in the browser, and the
 * renderer then never boots), so the agreement is checked here, where a
 * failing expectation names the cause, and again on the real build output by
 * scripts/csp/verify.ts.
 */
const template = readFileSync(appTemplatePath(), "utf8");
const csp = rendererCsp();
const directives = csp.directives ?? {};
const scriptSources = directives["script-src"] ?? [];

describe("TestRendererCsp", () => {
  it("has an inline script to hash", () => {
    // Guards the tests below: they are vacuous without a template script.
    expect(inlineScripts(template).length).toBeGreaterThan(0);
  });

  it("lists every hash variant of every template inline script", () => {
    for (const body of inlineScripts(template)) {
      for (const hash of scriptHashes(body)) {
        expect(scriptSources).toContain(hash);
      }
    }
  });

  it("uses hash mode", () => {
    // A nonce can not be delivered to a template script: the placeholder makes
    // the adapter-static fallback prerender throw.
    expect(csp.mode).toBe("hash");
  });

  it("does not mix unsafe-inline into script-src", () => {
    // A hash does not apply when 'unsafe-inline' is present: the browser
    // ignores the hashes and allows everything instead.
    expect(scriptSources).not.toContain("unsafe-inline");
  });

  it("keeps the template free of a CSP meta", () => {
    // SvelteKit writes the meta from kit.csp. A hand written one would be
    // enforced in addition to the generated one, and its hashes are stale by
    // definition.
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

  it("embeds the frontend at the loopback backend", () => {
    // The iframe is loaded from the python backend, whose port is configurable
    expect(directives["frame-src"]).toContain("http://127.0.0.1:*");
  });
});

describe("TestScriptHashes", () => {
  // The same fixtures as frontend/scripts/csp/inline-scripts.test.ts: the two
  // copies of the module have to stay in sync, and a divergence in the hash
  // algorithm fails here.
  it("hashes both variants of a padded script", () => {
    expect(scriptHashes('console.log("hi");\n')).toEqual([
      "sha256-K/ixJdFacbX6ef5xDK4NuRGnHmWJHicLyh1Otd14Uog=",
      "sha256-YyeTXEEDvdUn4SEEQwWcQj1xGgfkyT8LNTFXYzrV0nc=",
    ]);
  });

  it("hashes utf8 content as utf8", () => {
    expect(scriptHashes("const a = '🎉';")).toEqual(["sha256-RC39bB/NgRO9zmucFMfe/dPSgEWK6rThXEeFO7HNGlk="]);
  });
});

describe("TestVerifyPage", () => {
  const inline = "go();";
  const [rawHash, strippedHash] = scriptHashes(` ${inline} `);
  const page = (hashes: string[], body = ` ${inline} `) => {
    const sources = ["'self'", ...hashes.map((hash) => `'${hash}'`)].join(" ");
    return `<html><head><meta http-equiv="content-security-policy" content="script-src ${sources}"></head><body><script>${body}</script></body></html>`;
  };

  const templatePage = `<html><head></head><body><script> ${inline} </script></body></html>`;

  it("accepts a page whose inline scripts are covered", () => {
    expect(verifyPage(page([rawHash, strippedHash]), templatePage)).toEqual([]);
  });

  it("rejects a page with an unhashed inline script", () => {
    // one problem for the page script, one per template hash variant
    expect(verifyPage(page([]), templatePage)).toEqual([
      expect.stringContaining("not allowed by script-src"),
      `the template inline script is missing the hash ${rawHash}`,
      `the template inline script is missing the hash ${strippedHash}`,
    ]);
  });

  it("rejects a page without a CSP meta", () => {
    const problems = verifyPage("<html><body><script>go();</script></body></html>", templatePage);
    expect(problems).toEqual(["the page has no CSP meta tag, every inline script would be blocked"]);
  });

  it("requires both variants of a template script", () => {
    // A browser only matches the variant its own algorithm produces: the
    // renderer runs on Chromium 108 (electron 22), other sessions on current
    // browsers
    expect(verifyPage(page([rawHash]), templatePage)).toEqual([
      `the template inline script is missing the hash ${strippedHash}`,
    ]);
  });
});

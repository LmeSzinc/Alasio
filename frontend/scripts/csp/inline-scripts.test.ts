import { describe, expect, it } from "vitest";
import {
  cspMetaContent,
  inlineScriptHashes,
  inlineScripts,
  scriptHashes,
  scriptSrcSources,
  verifyPage,
} from "./inline-scripts.js";

// Content with leading and trailing whitespace, the shape of a script
// written on its own lines in app.html.
const PADDED = "\n    (() => {\n      go();\n    })();\n  ";
// The same content trimmed: both hash variants coincide.
const TRIMMED = "(() => {\n      go();\n    })();";

describe("TestScriptHashes", () => {
  it.each([
    // The expected values are the sha256 of the raw text and of the text with
    // the leading and trailing ASCII whitespace stripped.
    [
      'console.log("hi");\n',
      ["sha256-K/ixJdFacbX6ef5xDK4NuRGnHmWJHicLyh1Otd14Uog=", "sha256-YyeTXEEDvdUn4SEEQwWcQj1xGgfkyT8LNTFXYzrV0nc="],
    ],
    [
      PADDED,
      ["sha256-b1PT+C+w4WRBqyNHRkJiou5oYWXXy7EUwbZ0VlPoD1I=", "sha256-q1tf56/be+tUqelXiTDNnDYYtF1o83By+7eB7/LAaow="],
    ],
  ])("hashes both variants of %j", (content, expected) => {
    expect(scriptHashes(content)).toEqual(expected);
  });

  it("returns one hash when the content has no surrounding whitespace", () => {
    expect(scriptHashes('console.log("hi");')).toEqual(["sha256-YyeTXEEDvdUn4SEEQwWcQj1xGgfkyT8LNTFXYzrV0nc="]);
  });

  it("keeps inner whitespace significant", () => {
    expect(scriptHashes(TRIMMED)).not.toEqual(scriptHashes(TRIMMED.replace("  ", " ")));
  });

  it("hashes utf8 content as utf8", () => {
    // The emoji is hashed as its utf8 bytes, not as utf16 code units.
    expect(scriptHashes("const a = '🎉';")).toEqual(["sha256-RC39bB/NgRO9zmucFMfe/dPSgEWK6rThXEeFO7HNGlk="]);
  });
});

describe("TestInlineScripts", () => {
  it("collects inline scripts in document order", () => {
    const html = '<script>a();</script><p>x</p><script type="module">b();</script>';
    expect(inlineScripts(html)).toEqual(["a();", "b();"]);
  });

  it("skips scripts that load a url", () => {
    const html = '<script src="/a.js"></script><script>a();</script><script SRC="/b.js"></script>';
    expect(inlineScripts(html)).toEqual(["a();"]);
  });

  it("returns an empty list when there is no script", () => {
    expect(inlineScripts("<p>x</p>")).toEqual([]);
  });

  it("keeps the body verbatim, including entities", () => {
    // A script element is raw text: `&amp;` is not decoded by the parser, and
    // the hash is computed over the bytes as written.
    expect(inlineScripts("<script>if (a && b) {}</script>")).toEqual(["if (a && b) {}"]);
  });
});

describe("TestInlineScriptHashes", () => {
  it("deduplicates the variants shared by several scripts", () => {
    const html = "<script>a();</script><script>a();</script>";
    expect(inlineScriptHashes(html)).toEqual(scriptHashes("a();"));
  });

  it("returns nothing for a page without inline scripts", () => {
    expect(inlineScriptHashes('<script src="/a.js"></script>')).toEqual([]);
  });
});

describe("TestCspMetaContent", () => {
  it.each([
    ['<meta http-equiv="Content-Security-Policy" content="script-src \'self\'">', "script-src 'self'"],
    // The shape SvelteKit writes.
    ['<meta http-equiv="content-security-policy" content="script-src \'self\'">', "script-src 'self'"],
    // content before http-equiv
    ['<meta content="script-src \'self\'" http-equiv="Content-Security-Policy">', "script-src 'self'"],
    // unquoted http-equiv value
    ["<meta http-equiv=content-security-policy content=\"script-src 'self'\">", "script-src 'self'"],
  ])("reads %s", (html, expected) => {
    expect(cspMetaContent(html)).toBe(expected);
  });

  it("returns null without a meta", () => {
    expect(cspMetaContent("<p>x</p>")).toBe(null);
  });

  it("returns null for a meta with another http-equiv", () => {
    expect(cspMetaContent('<meta http-equiv="refresh" content="0">')).toBe(null);
  });
});

describe("TestScriptSrcSources", () => {
  it("reads script-src", () => {
    expect(scriptSrcSources("default-src 'self'; script-src 'self' 'sha256-abc='")).toEqual(["self", "sha256-abc="]);
  });

  it("prefers script-src-elem over script-src", () => {
    const csp = "script-src 'self' 'sha256-a='; script-src-elem 'self' 'sha256-b='";
    expect(scriptSrcSources(csp)).toEqual(["self", "sha256-b="]);
  });

  it("falls back to default-src", () => {
    expect(scriptSrcSources("default-src 'self'")).toEqual(["self"]);
  });

  it("returns nothing for a policy without a script directive", () => {
    expect(scriptSrcSources("style-src 'self'")).toEqual([]);
  });

  it("keeps the case of the hashes", () => {
    expect(scriptSrcSources("script-src 'sha256-AbC='")).toEqual(["sha256-AbC="]);
  });
});

describe("TestVerifyPage", () => {
  const inline = "go();";
  const [rawHash, strippedHash] = scriptHashes(` ${inline} `);
  const page = (hashes: string[], body = ` ${inline} `) => {
    const sources = ["'self'", ...hashes.map((hash) => `'${hash}'`)].join(" ");
    return `<html><head><meta http-equiv="content-security-policy" content="script-src ${sources}"></head><body><script>${body}</script></body></html>`;
  };

  it("accepts a page whose inline script is covered", () => {
    expect(verifyPage(page([rawHash, strippedHash]))).toEqual([]);
  });

  it("accepts a page whose inline script is covered by one variant", () => {
    // The scripts SvelteKit adds itself are hashed once, for the algorithm
    // the browser uses, so one matching variant is enough for the page.
    expect(verifyPage(page([strippedHash]))).toEqual([]);
  });

  it("rejects a page with an unhashed inline script", () => {
    const problems = verifyPage(page([]));
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("not allowed by script-src");
    expect(problems[0]).toContain(rawHash);
  });

  it("rejects a page without a CSP meta", () => {
    const problems = verifyPage("<html><body><script>go();</script></body></html>");
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("no CSP meta");
  });

  it("checks external scripts are not required to be hashed", () => {
    const html = `${page([rawHash, strippedHash])}<script src="/a.js"></script>`;
    expect(verifyPage(html)).toEqual([]);
  });

  it("requires every variant of a template script", () => {
    const template = `<html><head></head><body><script>${` ${inline} `}</script></body></html>`;
    const problems = verifyPage(page([strippedHash]), template);
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain(rawHash);
  });

  it("accepts a template whose variants are all listed", () => {
    const template = `<html><head></head><body><script>${` ${inline} `}</script></body></html>`;
    expect(verifyPage(page([rawHash, strippedHash]), template)).toEqual([]);
  });
});

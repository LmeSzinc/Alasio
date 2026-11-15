import { createHash } from "node:crypto";

/**
 * Inline script hashing for the Content-Security-Policy.
 *
 * The renderer template (renderer/app.html) carries one inline script that has
 * to run before the first paint (it applies the host theme), so the policy
 * must allow it by hash: 'unsafe-inline' is not an option, and a nonce can not
 * be used here (a `%sveltekit.nonce%` placeholder makes the adapter-static
 * fallback prerender throw, and the fallback page is the whole app).
 *
 * SvelteKit's `kit.csp` (mode 'hash') hashes every script it renders itself,
 * but it never parses the app template: the hashes of the template scripts
 * have to be supplied through the csp directives (scripts/csp/config.ts), and
 * the built page has to be checked against them after every build
 * (scripts/csp/verify.ts).
 *
 * This module is a copy of frontend/scripts/csp/inline-scripts.js: the two
 * packages are installed and built independently (each has its own
 * pnpm-lock), so they can not share a module. Keep the copies in sync, the
 * tests of both sides pin the same fixtures.
 */

// Matches a script element and captures its attributes and its raw body.
const SCRIPT_RE = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
// Matches a script element that loads its code from a url.
const SRC_ATTR_RE = /\bsrc\s*=/i;
// Matches the CSP meta tag regardless of the attribute order and the case.
const CSP_META_RE = /<meta\b[^>]*http-equiv\s*=\s*["']?content-security-policy["']?[^>]*>/i;
// Matches the double quoted content attribute inside a meta tag.
const CONTENT_ATTR_RE = /\bcontent\s*=\s*"([^"]*)"/i;

/**
 * Base64 encoded sha256 of a string, in the CSP source format.
 *
 * @param text Script content to hash
 * @returns Hash source without the quotes
 */
function sha256(text: string): `sha256-${string}` {
  return `sha256-${createHash("sha256").update(text, "utf8").digest("base64")}`;
}

/**
 * The hash variants of one inline script content.
 *
 * The variants are the raw content and the content with the leading and
 * trailing ASCII whitespace stripped (CSP3). They are the same string when the
 * content is already trimmed, in which case a single hash is returned.
 *
 * @param content Raw body of the script element
 * @returns Hash sources, without quotes, raw first
 */
export function scriptHashes(content: string): `sha256-${string}`[] {
  const stripped = content.replace(/^[\t\n\f\r ]+|[\t\n\f\r ]+$/g, "");
  return [...new Set([content, stripped])].map(sha256);
}

/**
 * The bodies of every inline script of a document, in document order.
 *
 * Scripts with a `src` attribute are skipped: they are allowed by the 'self'
 * source, not by a hash. The bodies are the raw text between the tags, which
 * is exactly what a browser hashes (a script element is raw text, its content
 * is not entity decoded).
 *
 * @param html HTML document
 * @returns Raw script bodies
 */
export function inlineScripts(html: string): string[] {
  return [...html.matchAll(SCRIPT_RE)].filter(([, attrs]) => !SRC_ATTR_RE.test(attrs)).map(([, , body]) => body);
}

/**
 * Every hash variant of every inline script of a document.
 *
 * @param html HTML document
 * @returns Hash sources, deduplicated, without quotes
 */
export function inlineScriptHashes(html: string): `sha256-${string}`[] {
  const out: `sha256-${string}`[] = [];
  for (const body of inlineScripts(html)) {
    for (const hash of scriptHashes(body)) {
      if (!out.includes(hash)) out.push(hash);
    }
  }
  return out;
}

/**
 * The policy of the CSP meta tag of a document.
 *
 * @param html HTML document
 * @returns Content of the meta tag, null when the document has no CSP meta
 */
export function cspMetaContent(html: string): string | null {
  const meta = CSP_META_RE.exec(html);
  if (!meta) return null;
  const content = CONTENT_ATTR_RE.exec(meta[0]);
  return content ? content[1] : null;
}

/**
 * Split a policy into its directives.
 *
 * @param csp Content of a CSP meta tag or response header
 * @returns Directive name (lower case) to its sources. Sources keep their
 *     case (hashes are case sensitive) but lose their quotes.
 */
function directives(csp: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const part of csp.split(";")) {
    const tokens = part.trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) continue;
    const name = tokens[0].toLowerCase();
    if (!out.has(name)) {
      out.set(
        name,
        tokens.slice(1).map((source) => source.replace(/^'(.*)'$/, "$1")),
      );
    }
  }
  return out;
}

/**
 * The sources that apply to script elements.
 *
 * `script-src-elem` overrides `script-src` for script elements, and
 * `default-src` is the fallback when both are absent. This mirrors the
 * precedence a browser applies.
 *
 * @param csp Content of a CSP meta tag or response header
 * @returns Sources that allow script elements, empty when the policy has none
 */
export function scriptSrcSources(csp: string): string[] {
  const parsed = directives(csp);
  return parsed.get("script-src-elem") ?? parsed.get("script-src") ?? parsed.get("default-src") ?? [];
}

/**
 * Check a built page against the hashes it needs.
 *
 * A page is broken when an inline script has no matching hash: the browser
 * blocks it, and for the sveltekit bootstrap script that means the app never
 * starts.
 *
 * @param html The built page
 * @param template Source of the page template. When given, every hash variant
 *     of its inline scripts is required, so the page keeps working on both the
 *     raw (Chromium 108) and the CSP3 (stripped) hash algorithm. Scripts
 *     SvelteKit adds itself only have one variant, hence the weaker
 *     requirement for the page as a whole.
 * @returns One message per problem, empty when the page is fine
 */
export function verifyPage(html: string, template?: string): string[] {
  const csp = cspMetaContent(html);
  if (!csp) {
    return ["the page has no CSP meta tag, every inline script would be blocked"];
  }
  const sources = new Set(scriptSrcSources(csp));
  const problems: string[] = [];

  for (const [index, body] of inlineScripts(html).entries()) {
    const hashes = scriptHashes(body);
    if (!hashes.some((hash) => sources.has(hash))) {
      problems.push(`inline script #${index + 1} is not allowed by script-src (${hashes.join(", ")})`);
    }
  }

  if (template !== undefined) {
    for (const body of inlineScripts(template)) {
      for (const hash of scriptHashes(body)) {
        if (!sources.has(hash)) {
          problems.push(`the template inline script is missing the hash ${hash}`);
        }
      }
    }
  }

  return problems;
}

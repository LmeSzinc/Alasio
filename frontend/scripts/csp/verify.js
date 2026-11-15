import { readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cspMetaContent, inlineScripts, verifyPage } from "./inline-scripts.js";

/**
 * Post-build CSP check.
 *
 * SvelteKit writes the CSP meta of the fallback page (`kit.csp`, hash mode),
 * and a stale policy is silent and fatal: the browser blocks the inline
 * scripts, the sveltekit bootstrap script included, so the page stays blank.
 * The build must not be able to produce such a page, hence this gate: it runs
 * after `vite build` and exits non zero when a hash is missing.
 *
 * The backend derives the response header from this meta
 * (alasio/backend/app/frontend.py), so a wrong meta is also a wrong header.
 *
 * This is a static model of the browser. The blocking behaviour itself is
 * proven in the real engine by `pnpm run smoke:csp` (scripts/csp/browser-smoke.cjs).
 *
 * Run with: node scripts/csp/verify.js [page]
 */
const root = fileURLToPath(new URL("../..", import.meta.url));
const pagePath = resolve(root, process.argv[2] ?? "build/index.html");
const templatePath = resolve(root, "src/app.html");

let html;
try {
  html = readFileSync(pagePath, "utf8");
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`[csp] can not read ${relative(root, pagePath)}: ${message}`);
  console.error("[csp] run the build first");
  process.exit(2);
}

const template = readFileSync(templatePath, "utf8");
const problems = verifyPage(html, template);

if (problems.length > 0) {
  console.error(`[csp] ${relative(root, pagePath)} fails the content security policy check:`);
  for (const problem of problems) {
    console.error(`[csp]   ${problem}`);
  }
  const csp = cspMetaContent(html);
  console.error(`[csp] policy: ${csp ?? "<none>"}`);
  process.exit(1);
}

const count = inlineScripts(html).length;
console.log(`[csp] ${relative(root, pagePath)}: ${count} inline script(s), every hash covered`);

import { existsSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { extractFile } from "@electron/asar";
import { appTemplatePath } from "./config.ts";
import { cspMetaContent, inlineScripts, verifyPage } from "./inline-scripts.ts";

/**
 * Verify the Content-Security-Policy of the packaged archive.
 *
 * The standalone release/app.asar can be shipped on its own ("small changes
 * without repacking the whole electron app"), and an archive that was packed
 * before the last `vite build` carries a page whose inline script hashes no
 * longer match: the renderer would then fail to boot at the user, while the
 * build on this machine looked fine. This gate reads the page back out of the
 * archive and checks it, so a stale archive can not be shipped silently.
 *
 * Run with: tsx scripts/csp/verify-asar.ts [archive]
 */
const root = fileURLToPath(new URL("../..", import.meta.url));
const archivePath = resolve(root, process.argv[2] ?? "release/app.asar");
// pack-asar.ts adds the files relative to the webapp root. The path is joined
// with the platform separator: @electron/asar walks the archive header with
// path.sep, so a forward slashed path does not resolve on Windows.
const pageEntry = join("dist", "renderer", "index.html");

if (!existsSync(archivePath)) {
  console.error(`[csp] ${relative(root, archivePath)} does not exist`);
  console.error("[csp] run `pnpm run build` first");
  process.exit(2);
}

let html: string;
try {
  html = extractFile(archivePath, pageEntry).toString("utf8");
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`[csp] ${pageEntry} is not readable from ${relative(root, archivePath)}: ${message}`);
  process.exit(1);
}

const template = readFileSync(appTemplatePath(), "utf8");
const problems = verifyPage(html, template);

if (problems.length > 0) {
  console.error(`[csp] ${relative(root, archivePath)} ships a page that fails the content security policy check:`);
  for (const problem of problems) {
    console.error(`[csp]   ${problem}`);
  }
  const csp = cspMetaContent(html);
  console.error(`[csp] policy: ${csp ?? "<none>"}`);
  console.error("[csp] repack it: tsx scripts/pack-asar.ts");
  process.exit(1);
}

const count = inlineScripts(html).length;
console.log(`[csp] ${relative(root, archivePath)}: ${count} inline script(s), every hash covered`);

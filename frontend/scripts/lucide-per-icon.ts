// Convert `import { Minus } from "@lucide/svelte"` global imports into
// per-icon imports like `import Minus from "@lucide/svelte/icons/minus"`.
//
// The root module re-exports the whole icon library (~1800 icons), so every
// build touching a global import must parse and compile every icon even
// though tree-shaking drops unused ones from the final bundle. Per-icon
// imports only pull in the icons actually used, cutting build time roughly
// in half.
//
// The icon name -> file name mapping is parsed from the package's own
// dist/icons/index.js (`export { default as X } from './x.svelte'`), which
// is the authoritative source: a naive kebab-case conversion is not
// reliable (e.g. Clock10 -> clock-10, ArrowDown01 -> arrow-down-0-1,
// Grid2x2 -> grid-2x2, Minimize2 -> minimize-2). Deprecated icon names
// (aliases/aliases.js, e.g. Edit -> SquarePen) are redirected to the
// renamed icon path while keeping the old local name, so template usages
// stay valid and no string/comment text is touched.
//
// Run with: npm run fix:lucide (scans src/)
// or: tsx scripts/lucide-per-icon.ts [dir...] [--check]
//   dir     Directory to scan, relative to the frontend root or absolute.
//           Defaults to "src". May be given multiple times.
//   --check Only report files that would change, do not write them.
//           Exits with code 1 when global imports are found.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { atomicWrite } from "./utils/atomic.ts";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const LUCIDE_INDEX = join(frontendRoot, "node_modules", "@lucide", "svelte", "dist", "icons", "index.js");
const LUCIDE_ALIASES = join(frontendRoot, "node_modules", "@lucide", "svelte", "dist", "aliases", "aliases.js");
const LUCIDE_ICONS_DIR = join(frontendRoot, "node_modules", "@lucide", "svelte", "dist", "icons");
const LUCIDE_MODULE = "@lucide/svelte";
const DEFAULT_DIRS = ["src"];
const SKIP_DIRS = new Set(["node_modules", ".svelte-kit", "dist", "build", ".git"]);
const SOURCE_EXTENSIONS = new Set([".svelte", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);

// Matches `import { ... } from "@lucide/svelte"` (single or multi line,
// optional trailing semicolon). The body must not contain braces, which
// also keeps the match from crossing unrelated statements such as
// `import { X } from "other-module"`. `import type { ... }` and
// `import * as ...` are intentionally not matched; nested type specs like
// `type { X }` are skipped.
const IMPORT_RE = /import\s*\{([^{}]*)\}\s*from\s*["']@lucide\/svelte["']\s*;?/g;

/**
 * Load the icon name -> file name mapping from the lucide package.
 *
 * The generated index.js is the authoritative source: kebab-case
 * conversion of the component name is ambiguous (Clock10 vs ArrowDown01
 * vs Grid2x2), so the mapping is read instead of guessed.
 *
 * Returns:
 *     Map[str, str]: Component name to icon file name (without extension)
 */
function loadIconMap() {
  let source;
  try {
    source = readFileSync(LUCIDE_INDEX, "utf-8");
  } catch {
    console.error(`[error] Cannot read ${LUCIDE_INDEX}. Install dependencies first (pnpm install).`);
    process.exit(2);
  }
  const map = new Map();
  const re = /export \{ default as (\w+) \} from '\.\/([\w-]+)\.svelte'/g;
  for (const match of source.matchAll(re)) {
    map.set(match[1], match[2]);
  }
  return map;
}

/**
 * Load the deprecated icon name -> new icon file name mapping.
 *
 * lucide keeps renamed icons available as deprecated aliases on the root
 * module (aliases/aliases.js). Each alias re-exports a shim file under
 * dist/icons/ (e.g. edit.js) which forwards to the renamed icon
 * (`export { default } from "./square-pen.svelte"`). Reading the shim
 * target gives the exact rename, more reliably than parsing the
 * @deprecated JSDoc text. Converting to the new name keeps types working
 * (shims have no .svelte.d.ts) and avoids deprecated usage.
 *
 * Args:
 *     iconMap (Map[str, str]): Icon name to file name mapping
 *
 * Returns:
 *     Map[str, dict]: Deprecated component name to
 *         {"file": new file name, "name": new component name}
 */
function loadDeprecatedMap(iconMap) {
  let source;
  try {
    source = readFileSync(LUCIDE_ALIASES, "utf-8");
  } catch {
    return new Map();
  }
  const fileToName = new Map();
  for (const [name, file] of iconMap) {
    fileToName.set(file, name);
  }
  const map = new Map();
  const re = /export \{\s*(?:\/\*\*[\s\S]*?\*\/\s*)?default as (\w+) \} from '\.\.\/icons\/([\w-]+)\.js'/g;
  for (const match of source.matchAll(re)) {
    const deprecatedName = match[1];
    const shimFile = match[2];
    if (iconMap.has(deprecatedName)) {
      continue;
    }
    let shim;
    try {
      shim = readFileSync(join(LUCIDE_ICONS_DIR, `${shimFile}.js`), "utf-8");
    } catch {
      continue;
    }
    const target = /export \{ default \} from "\.\/([\w-]+)\.svelte"/.exec(shim);
    const newName = fileToName.get(target?.[1]);
    if (target && newName) {
      map.set(deprecatedName, { file: target[1], name: newName });
    }
  }
  return map;
}

/**
 * Ensure a scan directory stays inside the project root.
 *
 * Each project ships its own copy of this script, so conversions are
 * confined to the project's own source tree.
 *
 * Args:
 *     root (str): Resolved absolute directory to scan
 */
function assertInsideProject(root) {
  const rel = relative(frontendRoot, root);
  if (rel === ".." || rel.startsWith(`..${sep}`)) {
    console.error(
      `[error] ${root} is outside the project root ${frontendRoot}. This script only fixes files inside its own project.`,
    );
    process.exit(2);
  }
}

/**
 * Walk a directory recursively and collect source files.
 *
 * Args:
 *     dir (str): Directory to walk
 *
 * Returns:
 *     list[str]: Relative paths of source files
 */
function walkSourceFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) {
      continue;
    }
    const file = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkSourceFiles(file));
    } else if (entry.isFile() && SOURCE_EXTENSIONS.has(extname(entry.name))) {
      files.push(file);
    } else if (entry.isSymbolicLink()) {
      const st = statSync(file);
      if (st.isDirectory()) {
        files.push(...walkSourceFiles(file));
      } else if (st.isFile() && SOURCE_EXTENSIONS.has(extname(entry.name))) {
        files.push(file);
      }
    }
  }
  return files;
}

/**
 * Split the body between `{ }` of an import into value and type specs.
 *
 * Args:
 *     body (str): Text between the braces, may span multiple lines
 *
 * Returns:
 *     dict: {"values": list[str], "types": list[str]} specifier names
 */
function parseSpecifiers(body) {
  const values = [];
  const types = [];
  let nextIsType = false;
  for (const raw of body.split(",")) {
    const spec = raw.trim();
    if (!spec) {
      continue;
    }
    if (nextIsType) {
      types.push(spec);
      nextIsType = false;
      continue;
    }
    if (spec === "type") {
      nextIsType = true;
      continue;
    }
    if (spec.startsWith("type ") || spec.startsWith("type{")) {
      types.push(spec.replace(/^type\s*/, ""));
      continue;
    }
    values.push(spec);
  }
  return { values, types };
}

/**
 * Build the replacement text for one global lucide import statement.
 *
 * Args:
 *     body (str): Specifier list between the braces
 *     indent (str): Leading whitespace of the import line
 *     iconMap (Map[str, str]): Icon name to file name mapping
 *     deprecatedMap (Map[str, str]): Deprecated name to new file name
 *
 * Returns:
 *     dict | None: {"text": replacement, "icons": list[str], "kept": list[str]}
 *         or None when the statement has no convertible icon imports
 */
function buildReplacement(body, indent, iconMap, deprecatedMap) {
  const { values, types } = parseSpecifiers(body);
  const icons = values.filter((name) => iconMap.has(name) || deprecatedMap.has(name));
  const kept = values.filter((name) => !iconMap.has(name) && !deprecatedMap.has(name));
  if (icons.length === 0) {
    return null;
  }
  const lines = [];
  for (const name of icons) {
    const file = iconMap.get(name);
    if (file) {
      lines.push(`import ${name} from "${LUCIDE_MODULE}/icons/${file}";`);
    } else {
      // Deprecated names are imported from the renamed icon path with the
      // old local name, so template usages keep working and no text in
      // strings or comments is touched by a global rename.
      const upgraded = deprecatedMap.get(name);
      lines.push(`import ${name} from "${LUCIDE_MODULE}/icons/${upgraded.file}";`);
    }
  }
  if (kept.length > 0) {
    lines.push(`import { ${kept.join(", ")} } from "${LUCIDE_MODULE}";`);
  }
  if (types.length > 0) {
    lines.push(`import type { ${types.join(", ")} } from "${LUCIDE_MODULE}";`);
  }
  // The first line keeps the original leading whitespace of the matched
  // statement; only the continuation lines need the indent prepended.
  for (let i = 1; i < lines.length; i++) {
    lines[i] = indent + lines[i];
  }
  return { text: lines.join("\n"), icons, kept };
}

/**
 * Convert global lucide imports in one file.
 *
 * Args:
 *     file (str): Absolute path of the file to convert
 *     iconMap (Map[str, str]): Icon name to file name mapping
 *     deprecatedMap (Map[str, dict]): Deprecated name to new icon mapping
 *     checkOnly (bool): Report without writing when True
 *
 * Returns:
 *     dict | None: {"icons": list[str], "kept": list[str]} conversions made,
 *         or None when the file has no convertible imports
 */
async function convertFile(file, iconMap, deprecatedMap, checkOnly) {
  const source = readFileSync(file, "utf-8");
  const icons = [];
  const kept = [];
  let changed = false;
  const output = source.replace(IMPORT_RE, (match, body, offset) => {
    const lineStart = source.lastIndexOf("\n", offset) + 1;
    const indent = source.slice(lineStart, offset);
    const replacement = buildReplacement(body, indent, iconMap, deprecatedMap);
    if (replacement === null) {
      return match;
    }
    changed = true;
    icons.push(...replacement.icons);
    kept.push(...replacement.kept);
    return replacement.text;
  });
  if (!changed) {
    return null;
  }
  if (!checkOnly) {
    await atomicWrite(file, output);
  }
  return { icons, kept };
}

/**
 * Print the script usage.
 */
function printHelp() {
  console.log(
    [
      "Usage: tsx scripts/lucide-per-icon.ts [options] [dir...]",
      "",
      'Convert `import { X } from "@lucide/svelte"` global icon imports',
      "into per-icon imports (`@lucide/svelte/icons/x`).",
      "",
      "Options:",
      "  --check  Report files that would change without writing them.",
      "           Exits with code 1 when global imports are found.",
      "  -h, --help  Print this help.",
      "",
      "Directories are resolved relative to the frontend root; absolute",
      "paths are accepted too, but must stay inside the frontend project.",
      "Defaults to src/.",
    ].join("\n"),
  );
}

/**
 * Main entry: parse arguments, scan directories, convert imports.
 */
async function main() {
  const args = process.argv.slice(2);
  let checkOnly = false;
  const dirs = [];
  for (const arg of args) {
    if (arg === "--check") {
      checkOnly = true;
    } else if (arg === "-h" || arg === "--help") {
      printHelp();
      process.exit(0);
    } else {
      dirs.push(arg);
    }
  }
  const scanDirs = dirs.length > 0 ? dirs : DEFAULT_DIRS;

  const iconMap = loadIconMap();
  const deprecatedMap = loadDeprecatedMap(iconMap);
  let convertedFiles = 0;
  let convertedIcons = 0;
  let found = false;
  for (const dir of scanDirs) {
    const root = resolve(frontendRoot, dir);
    assertInsideProject(root);
    let files;
    try {
      files = walkSourceFiles(root);
    } catch (error) {
      console.error(`[error] Cannot scan ${dir}: ${error.message}`);
      process.exit(2);
    }
    for (const file of files) {
      const result = await convertFile(file, iconMap, deprecatedMap, checkOnly);
      if (result === null) {
        continue;
      }
      found = true;
      convertedFiles += 1;
      convertedIcons += result.icons.length;
      console.log(relative(frontendRoot, file));
      for (const name of result.icons) {
        const upgraded = deprecatedMap.get(name);
        if (upgraded) {
          console.log(
            `  ${name} -> ${LUCIDE_MODULE}/icons/${upgraded.file} (deprecated name, renamed to ${upgraded.name})`,
          );
        } else {
          console.log(`  ${name} -> ${LUCIDE_MODULE}/icons/${iconMap.get(name)}`);
        }
      }
      for (const name of result.kept) {
        console.log(`  ${name} (not converted, kept on ${LUCIDE_MODULE})`);
      }
    }
  }

  if (!found) {
    console.log("[ok] No global lucide imports found.");
    process.exit(0);
  }
  const verb = checkOnly ? "would convert" : "converted";
  console.log(`[${checkOnly ? "check" : "fix"}] ${verb} ${convertedFiles} file(s), ${convertedIcons} icon(s).`);
  if (checkOnly) {
    process.exit(1);
  }
}

main();

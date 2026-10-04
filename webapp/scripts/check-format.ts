// Check and format frontend code, the frontend counterpart of
// `alasio.codegen.ruff`. Run it after editing frontend code:
//
// 1. I18n artifacts are regenerated from source usage (same as
//    `pnpm run i18ngen`), so new `t.Module.Key()` usages in the given
//    files get their JSON translations and generated TS artifacts.
// 2. Prettier formats the given files (supports wildcards and glob
//    patterns); only files that actually change are written.
// 3. `svelte-kit sync` regenerates generated types, then `svelte-check`
//    type-checks the given files (and the files they import), not the
//    whole project, so the check stays fast.
//
// All steps are bound together and must pass: exits with code 1 when
// any of them fails, so the result is visible to the caller.
//
// The script is self-locating (it resolves the project root from its
// own path), so the same file is deployed to both frontend/ and
// webapp/scripts/. i18n handling covers both layouts: frontend ships a
// single i18nConfig, webapp ships renderer and main configs.
//
// Run with: pnpm run codegen -- <files...>
// or: tsx scripts/check-format.ts <files...>
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, unlinkSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import fg from "fast-glob";
import { getFileInfo, format as prettierFormat, resolveConfig } from "prettier";
import type { I18nConfig } from "./i18n/config.ts";
import { atomicWrite } from "./utils/atomic.ts";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const SVELTE_KIT_BIN = join(frontendRoot, "node_modules", "@sveltejs", "kit", "svelte-kit.js");
const SVELTE_CHECK_BIN = join(frontendRoot, "node_modules", "svelte-check", "bin", "svelte-check");
const PRETTIER_IGNORE = join(frontendRoot, ".prettierignore");
const SVELTE_KIT_DIR = join(frontendRoot, ".svelte-kit");
// Temporary tsconfig that limits svelte-check to the given files.
// The pid suffix keeps concurrent runs from clobbering each other.
const CODEGEN_TSCONFIG = join(SVELTE_KIT_DIR, `codegen-tsconfig-${process.pid}.json`);
// Ambient declaration files (relative to .svelte-kit) that provide the
// `$app/*`, `$env/*`, vite/client and route `$types` declarations. They
// must be part of the program or module resolution breaks.
const AMBIENT_INCLUDES = ["ambient.d.ts", "env.d.ts", "non-ambient.d.ts", "./types/**/$types.d.ts"];
// Extensions that svelte-check can type-check. html/css/json files are
// still prettier-formatted but cannot be part of a TS program.
const CHECK_EXTENSIONS = new Set([".svelte", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"]);

/**
 * Print the script usage.
 */
function printHelp(): void {
  console.log(
    [
      "Usage: pnpm run codegen -- [options] [file...]",
      "",
      "Prettier format the given files, then run svelte-check over only",
      "those files (plus the files they import). Files support wildcards",
      "and glob patterns; paths are resolved relative to the frontend",
      "root.",
      "",
      "Options:",
      "  -h, --help  Print this help.",
      "",
      "Examples:",
      "  pnpm run codegen -- src/lib/foo.ts",
      '  pnpm run codegen -- "src/routes/**/*.svelte"',
    ].join("\n"),
  );
}

/**
 * Expand glob patterns, preserving input order and dropping duplicates.
 *
 * Patterns that match nothing keep their literal path, so a mistyped
 * file is reported as not found instead of silently disappearing.
 *
 * Args:
 *     paths (list[str]): File paths or glob patterns
 *
 * Returns:
 *     list[str]: Absolute paths, deduplicated
 */
function expandPaths(paths: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of paths) {
    // fast-glob expects forward slashes
    const pattern = p.replaceAll("\\", "/");
    const matches = fg.sync(pattern, {
      cwd: frontendRoot,
      onlyFiles: true,
      absolute: true,
      // A directory held open by another process (the running dev server
      // watching the tree on Windows) makes the walk raise EPERM; skip such
      // directories instead of failing the whole codegen, a mistyped file is
      // still reported by the literal-path fallback below
      suppressErrors: true,
    });
    if (matches.length > 0) {
      for (const m of matches) {
        if (!seen.has(m)) {
          seen.add(m);
          out.push(m);
        }
      }
    } else {
      const abs = resolve(frontendRoot, p);
      if (!seen.has(abs)) {
        seen.add(abs);
        out.push(abs);
      }
    }
  }
  return out;
}

/**
 * Drop files that prettier would not process: files ignored by
 * .prettierignore and files without an inferred parser.
 *
 * The ignore path must be passed explicitly: the prettier API does not
 * look up .prettierignore by itself (only the CLI does).
 *
 * Args:
 *     files (list[str]): Absolute paths
 *
 * Returns:
 *     list[str]: Absolute paths prettier can format
 */
async function filterFiles(files: string[]): Promise<string[]> {
  const out: string[] = [];
  for (const file of files) {
    let info;
    try {
      info = await getFileInfo(file, { ignorePath: PRETTIER_IGNORE });
    } catch {
      // Unreadable path, keep it so formatFile reports the failure
      out.push(file);
      continue;
    }
    if (info.ignored || !info.inferredParser) {
      continue;
    }
    out.push(file);
  }
  return out;
}

/**
 * Prettier format a single file in place.
 *
 * Only writes the file when the formatted output differs, so unchanged
 * files keep their timestamps. A syntax error or missing file is
 * reported and counts as a failure, while other files are still
 * processed.
 *
 * Args:
 *     file (str): Absolute path of the file to format
 *
 * Returns:
 *     bool: True when the file was formatted without errors
 */
async function formatFile(file: string): Promise<boolean> {
  let filename = file;
  try {
    filename = relative(frontendRoot, file).replaceAll("\\", "/");
  } catch {
    // Different drive on Windows, keep the absolute path
  }
  console.log(`Formatting: ${filename}`);
  let source: string;
  try {
    source = readFileSync(file, "utf8");
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      console.log("File not found");
    } else {
      console.log(`Failed to read: ${(error as Error).message}`);
    }
    return false;
  }
  const config = await resolveConfig(file, { editorconfig: true });
  let formatted: string;
  try {
    formatted = await prettierFormat(source, { filepath: file, ...(config ?? {}) });
  } catch (error) {
    console.log(`Failed to format: ${(error as Error).message}`);
    return false;
  }
  if (formatted === source) {
    console.log("All good");
  } else {
    try {
      await atomicWrite(file, formatted);
    } catch (error) {
      console.log(`Failed to write: ${(error as Error).message}`);
      return false;
    }
    console.log("Fixed");
  }
  return true;
}

/**
 * Collect project declaration files (.d.ts) to include in the temp
 * tsconfig.
 *
 * Input files are type-checked against the project's global
 * declarations (e.g. `window.electronAPI` in webapp/renderer/electron.d.ts).
 * A full check picks them up through the project tsconfig, but the
 * temp tsconfig only lists ambient files and the input files, so the
 * declarations must be added explicitly. .d.ts files are type
 * environment only: with skipLibCheck they produce no diagnostics, so
 * the check scope stays limited to the input files and their imports.
 *
 * Returns:
 *     list[str]: Absolute paths of declaration files
 */
function collectDeclarations(): string[] {
  const files = fg.sync(["**/*.d.ts"], {
    cwd: frontendRoot,
    absolute: true,
    onlyFiles: true,
    // Same as expandPaths: a directory held open by the running dev server
    // must not fail the whole codegen
    suppressErrors: true,
    ignore: ["node_modules/**", ".svelte-kit/**", "dist/**", "release/**", "build/**"],
  });
  files.sort();
  return files;
}

/**
 * Write the temporary tsconfig that limits svelte-check to the given
 * files.
 *
 * The tsconfig extends the project tsconfig so compiler options (paths,
 * skipLibCheck, ...) stay intact, then overrides `include` with the
 * ambient declaration files, the project's .d.ts files, and the
 * formatted files. `exclude` is cleared so explicitly listed files
 * (e.g. service-worker) are checked too. Paths are relative to
 * .svelte-kit where the temp file lives.
 *
 * Args:
 *     files (list[str]): Absolute paths of the files to check
 */
async function writeCodegenTsconfig(files: string[]): Promise<void> {
  // On a fresh checkout .svelte-kit does not exist yet; svelte-kit sync
  // would create it later, but the temp tsconfig must be written first.
  mkdirSync(SVELTE_KIT_DIR, { recursive: true });
  const toInclude = (file: string) => relative(SVELTE_KIT_DIR, file).replaceAll("\\", "/");
  const include = [...AMBIENT_INCLUDES, ...collectDeclarations().map(toInclude), ...files.map(toInclude)];
  const tsconfig = {
    extends: "../tsconfig.json",
    include,
    exclude: [],
  };
  await atomicWrite(CODEGEN_TSCONFIG, `${JSON.stringify(tsconfig, null, 2)}\n`);
}

/**
 * Update i18n artifacts from source usage.
 *
 * Runs the full i18n generation (same as `pnpm run i18ngen`): scans all
 * source files for `t.Module.Key()` usages and updates the JSON
 * translations and generated TS artifacts. The full scan is required —
 * an incremental scan of only the input files would rebuild the module
 * cache from partial usage and garbage-collect modules and keys that
 * other files still use.
 *
 * Must run before svelte-check: files that use newly added keys fail
 * type-checking until the generated artifacts are updated.
 *
 * Returns:
 *     bool: True when i18n generation succeeded
 */
async function runI18ngen(): Promise<boolean> {
  console.log("I18n: sync translations from source usage");
  try {
    // Dynamic import after chdir: config modules may capture cwd at
    // module load time. Pin config.cwd explicitly so resolvePath() and
    // glob() agree on the project root.
    const { I18nGenerator } = await import("./i18n/core.ts");
    const configModule: Record<string, unknown> = await import("./i18n/config.ts");
    // frontend ships a single i18nConfig; webapp ships renderer and
    // main configs (svelte + node mode). The same script covers both.
    const configs = ["i18nConfig", "rendererI18nConfig", "mainI18nConfig"]
      .map((name) => configModule[name])
      .filter((c): c is I18nConfig => typeof c === "object" && c !== null);
    for (const config of configs) {
      config.cwd = frontendRoot;
      await new I18nGenerator(config).init();
    }
    return true;
  } catch (error) {
    console.log(`i18n generation failed: ${(error as Error).message}`);
    return false;
  }
}

/**
 * Run `svelte-kit sync` followed by `svelte-check` over the files
 * listed in the temporary tsconfig.
 *
 * Returns:
 *     bool: True when both commands exit with 0
 */
function runSvelteCheck(): boolean {
  const tsconfigName = relative(frontendRoot, CODEGEN_TSCONFIG).replaceAll("\\", "/");
  console.log(`Svelte check: svelte-kit sync && svelte-check --tsconfig ${tsconfigName}`);
  const sync = spawnSync(process.execPath, [SVELTE_KIT_BIN, "sync"], {
    cwd: frontendRoot,
    stdio: "inherit",
  });
  if (sync.error) {
    console.log(`svelte-kit sync failed to start: ${sync.error.message} (run pnpm install first?)`);
    return false;
  }
  if (sync.status !== 0) {
    console.log("svelte-kit sync failed");
    return false;
  }
  const check = spawnSync(process.execPath, [SVELTE_CHECK_BIN, "--tsconfig", CODEGEN_TSCONFIG], {
    cwd: frontendRoot,
    stdio: "inherit",
  });
  if (check.error) {
    console.log(`svelte-check failed to start: ${check.error.message} (run pnpm install first?)`);
    return false;
  }
  return check.status === 0;
}

/**
 * Main entry: parse arguments, format files, run svelte-check.
 */
async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    printHelp();
    process.exit(1);
  }
  if (args.includes("-h") || args.includes("--help")) {
    printHelp();
    process.exit(0);
  }
  // pnpm passes the `--` separator through to the script, drop it
  const files = args.filter((arg) => arg !== "--");
  if (files.length === 0) {
    printHelp();
    process.exit(1);
  }
  // Resolve module plugins and relative paths from the frontend root
  process.chdir(frontendRoot);

  // Update i18n artifacts before formatting and checking, so new
  // t.Module.Key() usages in the input files are reflected in the
  // generated files that svelte-check reads.
  let i18nFailed = false;
  if (!(await runI18ngen())) {
    i18nFailed = true;
  }

  const checked = await filterFiles(expandPaths(files));
  console.log(`Prettier format ${checked.length} files`);
  const formatted: string[] = [];
  let failed = 0;
  for (const file of checked) {
    if (await formatFile(file)) {
      formatted.push(file);
    } else {
      failed += 1;
    }
    console.log();
  }

  // html/css/json files cannot be part of a TS program; prettier
  // formatted them but there is nothing to type-check
  const checkable = formatted.filter((file) => CHECK_EXTENSIONS.has(extname(file).toLowerCase()));
  let checkOk = true;
  if (checkable.length > 0) {
    try {
      await writeCodegenTsconfig(checkable);
      checkOk = runSvelteCheck();
    } finally {
      try {
        unlinkSync(CODEGEN_TSCONFIG);
      } catch {
        // Temp file may already be gone
      }
    }
  } else if (formatted.length > 0) {
    console.log("No type-checkable files, skip svelte-check");
  } else {
    console.log("No files to check, skip svelte-check");
  }

  if (i18nFailed || failed > 0 || !checkOk) {
    const parts = [];
    if (i18nFailed) {
      parts.push("i18n generation failed");
    }
    if (failed > 0) {
      parts.push(`${failed} file(s) failed to format`);
    }
    if (!checkOk) {
      parts.push("svelte-check failed");
    }
    console.log(`Done with errors: ${parts.join(", ")}`);
    process.exit(1);
  }
  console.log("Done, all good");
}

main().catch((error) => {
  console.error("check-format failed:", error);
  process.exit(1);
});

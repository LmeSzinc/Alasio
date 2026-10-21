#!/usr/bin/env node
// Keep this package version in sync with the repository pyproject.toml.
// Run with --check to verify without writing (used by the `check` script).
import { readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(projectRoot, "..");
const pyproject = join(repoRoot, "pyproject.toml");
const manifestFile = join(projectRoot, "package.json");

/**
 * Read [project].version from pyproject.toml without a TOML dependency.
 *
 * The project table is small and the version is a plain string, so the
 * table is isolated first and the version key is matched inside it.
 *
 * @returns {string} The project version
 */
function readProjectVersion() {
  const text = readFileSync(pyproject, "utf-8").replace(/^\uFEFF/, "");
  const header = /^\[project\]\s*$/m.exec(text);
  if (!header) {
    throw new Error(`${relative(repoRoot, pyproject)}: [project] table not found`);
  }
  const body = text.slice(header.index + header[0].length);
  const nextHeader = /^\[/m.exec(body);
  const table = nextHeader ? body.slice(0, nextHeader.index) : body;
  const match = /^\s*version\s*=\s*["']([^"']+)["']/m.exec(table);
  if (!match) {
    throw new Error(`${relative(repoRoot, pyproject)}: [project].version not found`);
  }
  return match[1];
}

/**
 * Write the manifest through a temp file and rename, so an interrupted
 * run never leaves a half-written package.json behind.
 *
 * @param {string} file Absolute path of the package.json
 * @param {object} data Parsed manifest
 */
function writeJsonAtomic(file, data) {
  const tmp = `${file}.tmp-${process.pid}`;
  try {
    writeFileSync(tmp, `${JSON.stringify(data, null, 2)}\n`, "utf-8");
    renameSync(tmp, file);
  } finally {
    rmSync(tmp, { force: true });
  }
}

const check = process.argv.includes("--check");
let version;
try {
  version = readProjectVersion();
} catch (err) {
  console.error(err.message);
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestFile, "utf-8"));
const name = relative(repoRoot, manifestFile);
if (manifest.version === version) {
  console.log(`${name}: ${version}`);
} else if (check) {
  console.error(`${name}: version ${manifest.version} does not match pyproject.toml ${version}`);
  console.error("Run `node scripts/sync-version.mjs` to update the package version.");
  process.exit(1);
} else {
  const previous = manifest.version;
  manifest.version = version;
  writeJsonAtomic(manifestFile, manifest);
  console.log(`${name}: ${previous} -> ${version}`);
}

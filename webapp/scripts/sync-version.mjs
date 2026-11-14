#!/usr/bin/env node
// Keep this package version in sync with __version__ of alasio/__init__.py.
// Run with --check to verify without writing (used by the `check` script).
//
// The package source is the single owner of the version: pyproject.toml reads
// it with [tool.setuptools.dynamic] version = {attr = "alasio.__version__"},
// and so does the wheel builder, which parses the file from the git commit.
import { readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(projectRoot, "..");
const versionFile = join(repoRoot, "alasio", "__init__.py");
const manifestFile = join(projectRoot, "package.json");

/**
 * Read __version__ of the package source, without importing the package.
 *
 * Only a plain string assignment is accepted: that is what the wheel builder
 * reads statically from the commit, so a computed value would leave the wheel
 * and this manifest disagreeing.
 *
 * @returns {string} The package version
 */
function readPackageVersion() {
  const source = readFileSync(versionFile, "utf-8");
  const match = /^__version__[ \t]*(?::[^=\n]+)?=[ \t]*["']([^"']+)["']/m.exec(source);
  if (!match) {
    throw new Error(`${relative(repoRoot, versionFile)}: no plain string __version__ assignment`);
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
  version = readPackageVersion();
} catch (err) {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
}

const manifest = JSON.parse(readFileSync(manifestFile, "utf-8"));
const name = relative(repoRoot, manifestFile);
if (manifest.version === version) {
  console.log(`${name}: ${version}`);
} else if (check) {
  console.error(`${name}: version ${manifest.version} does not match alasio/__init__.py ${version}`);
  console.error("Run `node scripts/sync-version.mjs` to update the package version.");
  process.exit(1);
} else {
  const previous = manifest.version;
  manifest.version = version;
  writeJsonAtomic(manifestFile, manifest);
  console.log(`${name}: ${previous} -> ${version}`);
}

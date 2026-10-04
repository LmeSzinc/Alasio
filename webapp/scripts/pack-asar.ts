// Pack the vite build output (dist/) into app.asar. A standalone archive is
// always generated at the release root, and existing archives inside packaged
// apps are replaced, so small changes can be shipped without repacking the
// whole electron app.
//
// The archive layout replicates electron-builder's `files` config in
// electron-builder.yml (dist/**/* plus package.json at the archive root).
// Run with: tsx scripts/pack-asar.ts
import { existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { createPackageFromFiles } from "@electron/asar";
import { replaceTmp, toTmpFile } from "./utils/atomic.ts";

const webappRoot = fileURLToPath(new URL("..", import.meta.url));
const distDir = join(webappRoot, "dist");
const releaseDir = join(webappRoot, "release");

/**
 * Collect all regular files under a directory recursively.
 *
 * Symlinks are resolved with stat so linked files and directories are
 * followed, matching how the asar package walks its input.
 *
 * Args:
 *     dir (str): Directory to walk
 *
 * Returns:
 *     list[str]: Absolute paths of regular files
 */
function walkFiles(dir: string): string[] {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const file = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkFiles(file));
    } else if (entry.isFile()) {
      files.push(file);
    } else if (entry.isSymbolicLink()) {
      const st = statSync(file);
      if (st.isDirectory()) {
        files.push(...walkFiles(file));
      } else if (st.isFile()) {
        files.push(file);
      }
    }
  }
  return files;
}

/**
 * Find every existing app.asar archive under a directory recursively.
 *
 * Args:
 *     dir (str): Directory to search
 *
 * Returns:
 *     list[str]: Absolute paths of app.asar files
 */
function findAsarFiles(dir: string): string[] {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const file = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...findAsarFiles(file));
    } else if (entry.isFile() && entry.name === "app.asar") {
      files.push(file);
    }
  }
  return files;
}

/**
 * Replace an app.asar archive atomically with a freshly packed one.
 *
 * The archive is written next to the target and renamed over it, so an
 * interrupted run never leaves a half-written app.asar in place. The rename
 * retries while a running Electron instance still holds the target archive
 * open (Windows).
 *
 * Args:
 *     target (str): Absolute path of the app.asar to replace
 *     files (list[str]): Archive entries, relative to webappRoot
 */
async function replaceAsar(target: string, files: string[]): Promise<void> {
  // The temp archive uses the atomic util naming (random ID), so concurrent
  // runs do not clobber each other
  const tmp = toTmpFile(target);
  try {
    await createPackageFromFiles(webappRoot, tmp, files);
    await replaceTmp(tmp, target);
    console.log(`Updated ${relative(webappRoot, target)}`);
  } finally {
    rmSync(tmp, { force: true });
  }
}

async function main() {
  if (!existsSync(distDir)) {
    console.error("dist directory not found, run vite build first.");
    process.exitCode = 1;
    return;
  }
  // Always generate a standalone app.asar at the release root, so the latest
  // build can be shipped without repacking the whole electron app.
  mkdirSync(releaseDir, { recursive: true });
  const files = [...walkFiles(distDir).map((file) => relative(webappRoot, file)), "package.json"];
  const standaloneAsar = join(releaseDir, "app.asar");
  await replaceAsar(standaloneAsar, files);
  // Keep the app.asar archives inside existing packaged apps up to date.
  for (const target of findAsarFiles(releaseDir)) {
    if (target === standaloneAsar) {
      // Already generated above.
      continue;
    }
    await replaceAsar(target, files);
  }
}

main();

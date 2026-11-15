import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import * as yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import { SUPPORTED_LANGS } from "./i18ngen/constants";

/**
 * Guard for the packaged electron locale packs.
 *
 * electron-builder prunes `locales/` down to the entries of
 * `electronLanguages` in electron-builder.yml. Chromium resolves the
 * application locale against the packs that survive, so a supported GUI
 * language whose pack was pruned silently degrades app.getLocale() to
 * en-US for every user of that language — the display language, the tray
 * and the embedded frontend's first paint all follow that value (the
 * frontend reads it through navigator.languages, which puts the
 * application locale first).
 *
 * The rule is file-name based: an entry keeps a pack when the entry equals
 * the pack name or starts with `<pack>-` / `<pack>_`, which is how a single
 * `ja-JP` entry keeps `ja.pak` and how the underscore variants cover the
 * macOS `*.lproj` directory names.
 *
 * These tests read the real electron distribution, so a mismatch between
 * the configured entries and the shipped packs fails here instead of
 * silently shipping an English-only client.
 *
 * The distribution only exists when the electron binary was downloaded, and
 * the ci webapp job installs with ELECTRON_SKIP_BINARY_DOWNLOAD=1: the checks
 * that need the packs are skipped there and run in the smoke job, which
 * installs the very binary this client ships.
 */

const webappRoot = fileURLToPath(new URL("..", import.meta.url));
const localeDir = join(webappRoot, "node_modules", "electron", "dist", "locales");
const hasDistribution = existsSync(localeDir);

/** Pack name without its extension, lowercase (the comparison space). */
function packLanguage(file: string): string {
  return file.replace(/\.pak$/, "").toLowerCase();
}

/**
 * Apply the electron-builder keep rule to a list of pack files.
 *
 * Args:
 *     wanted (list[str]): Configured electronLanguages entries
 *     packs (list[str]): Pack file names in the electron distribution
 *
 * Returns:
 *     list[str]: Pack file names that survive packaging
 */
function keptPacks(wanted: readonly string[], packs: readonly string[]): string[] {
  const entries = wanted.map((entry) => entry.trim().toLowerCase()).filter((entry) => entry.length > 0);
  if (entries.length === 0) {
    return [...packs];
  }
  return packs.filter((pack) => {
    const language = packLanguage(pack);
    return entries.some(
      (entry) => entry === language || entry.startsWith(`${language}-`) || entry.startsWith(`${language}_`),
    );
  });
}

/**
 * Read the configured pack entries from electron-builder.yml.
 *
 * Returns:
 *     list[str]: `electronLanguages` entries, empty when the field is absent
 *         (absent means nothing is pruned)
 */
function configuredLanguages(): string[] {
  const file = join(webappRoot, "electron-builder.yml");
  const config = yaml.load(readFileSync(file, "utf-8")) as { electronLanguages?: string[] } | undefined;
  return config?.electronLanguages ?? [];
}

describe("TestPackagedLocalePacks", () => {
  it("configures packs that all belong to a supported GUI language", () => {
    const supported = SUPPORTED_LANGS.map((lang) => lang.toLowerCase());
    const bases = new Set(supported.map((lang) => lang.split("-")[0]));
    for (const entry of configuredLanguages()) {
      const normalized = entry.trim().toLowerCase().replace(/_/g, "-");
      const belongs = supported.includes(normalized) || bases.has(normalized.split("-")[0]);
      expect(belongs, `electronLanguages entry ${entry} matches no supported language`).toBe(true);
    }
  });
});

// the pack list is a property of the electron build, it cannot be known without
// the binary: these are the checks the smoke job exists for
describe.skipIf(!hasDistribution)("TestShippedLocalePacks", () => {
  it("keeps a locale pack for every supported GUI language", () => {
    const packs = readdirSync(localeDir);
    const kept = keptPacks(configuredLanguages(), packs).map(packLanguage);
    for (const lang of SUPPORTED_LANGS) {
      const exact = lang.toLowerCase();
      const base = exact.split("-")[0];
      // Chromium picks the exact pack when it exists and the bare language
      // pack otherwise (ja-JP -> ja, es-ES -> es)
      const resolvable = kept.some((pack) => pack === exact || pack === base);
      expect(resolvable, `no locale pack kept for ${lang}; kept packs: ${kept.join(", ")}`).toBe(true);
    }
  });

  it("still prunes the packs of unsupported languages", () => {
    // The point of the entry list is a small bundle; leaving it absent (or
    // listing everything) would ship 26MB of strings the client never uses.
    const packs = readdirSync(localeDir);
    const kept = keptPacks(configuredLanguages(), packs);
    expect(kept.length).toBeLessThan(packs.length);
  });
});

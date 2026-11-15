/**
 * System language matching.
 *
 * The host decides the display language from the OS language while the
 * config value is 'system', so the locale tags the OS reports have to be
 * matched against the supported GUI languages.
 *
 * Pure functions only: `app-state.ts` reads the electron APIs and passes
 * the values in, which keeps this module testable without starting
 * electron (the vitest node environment cannot load the electron module).
 */
import { DEFAULT_LANG, SUPPORTED_LANGS } from "./i18ngen/constants";

/**
 * Chinese cannot be resolved by a base-language match: 'zh' is the base of
 * both Simplified and Traditional, so the first supported entry with that
 * base would always win and a Traditional Chinese user would get
 * Simplified. The variant subtag decides instead, mirroring the alias
 * table the backend negotiates Accept-Language with
 * (alasio/backend/locale/accept_language.py).
 */
const CHINESE_SCRIPTS: Record<string, string> = {
  hans: "zh-CN",
  hant: "zh-TW",
};

/** Regions that imply the script of a Chinese locale tag. */
const CHINESE_REGIONS: Record<string, string> = {
  cn: "zh-CN",
  sg: "zh-CN",
  tw: "zh-TW",
  hk: "zh-TW",
  mo: "zh-TW",
};

/**
 * Split a locale tag into its lowercase subtags.
 *
 * Normalizes what the OS APIs actually return: `_` instead of `-`
 * (Windows, macOS), the charset suffix `g_get_language_names` appends on
 * Linux ('zh_CN.UTF-8') and its modifier suffix ('sr_RS@latin').
 *
 * Args:
 *     tag (str): Locale tag as reported by the OS
 *
 * Returns:
 *     list[str]: Subtags, empty when the tag carries none
 */
function splitLocale(tag: string): string[] {
  const bare = tag.split(".")[0].split("@")[0];
  return bare
    .replace(/_/g, "-")
    .toLowerCase()
    .split("-")
    .filter((part) => part.length > 0);
}

/**
 * Resolve the Chinese variant of a locale tag from its script/region
 * subtags. A bare 'zh' has none and falls through to the base match, which
 * picks the first supported Chinese language (Simplified), the same way
 * the backend maps 'zh' to 'zh-CN'.
 *
 * Args:
 *     parts (list[str]): Subtags of the locale, base language first
 *
 * Returns:
 *     str: One of the supported Chinese languages, undefined when the tag
 *         does not name a variant
 */
function matchChineseVariant(parts: readonly string[]): string | undefined {
  for (const part of parts) {
    const lang = CHINESE_SCRIPTS[part] || CHINESE_REGIONS[part];
    if (lang) {
      return lang;
    }
  }
  return undefined;
}

/**
 * Match one locale tag against the supported GUI languages.
 *
 * Matching order, mirroring the backend negotiation:
 * 1. exact tag, case/separator insensitive: 'en-us' -> 'en-US'
 * 2. Chinese variant by script or region: 'zh-Hant-HK' -> 'zh-TW'
 * 3. base language: 'en-GB' -> 'en-US', 'es-419' -> 'es-ES'
 *
 * Args:
 *     tag (str): Locale tag as reported by the OS
 *
 * Returns:
 *     str: The matching supported language, undefined when none matches
 */
export function matchLocale(tag: string): string | undefined {
  const parts = splitLocale(tag);
  const [base] = parts;
  if (!base) {
    return undefined;
  }

  const normalized = parts.join("-");
  const exact = SUPPORTED_LANGS.find((lang) => lang.toLowerCase() === normalized);
  if (exact) {
    return exact;
  }

  if (base === "zh") {
    const variant = matchChineseVariant(parts.slice(1));
    if (variant) {
      return variant;
    }
  }

  return SUPPORTED_LANGS.find((lang) => lang.split("-")[0].toLowerCase() === base);
}

/**
 * Derive the display language from the OS language.
 *
 * The application locale is the primary source: it is the display
 * language the user picked in the OS settings. `app.getSystemLocale()` is
 * deliberately not used — it is the regional format (dates, numbers,
 * currency), which is a separate OS setting, so it can name a language the
 * UI does not use.
 *
 * The preferred system languages are a fallback for a display language no
 * supported GUI language matches. They are not the primary source even
 * though electron recommends them for exactly this purpose: their order is
 * not guaranteed to start with the active display language (measured on
 * Windows, where the language list can begin with 'en-US' while the
 * effective UI language is 'zh-CN').
 *
 * Args:
 *     locale (str): Application locale (`app.getLocale()`)
 *     preferred (list[str]): Preferred system languages, most preferred
 *         first (`app.getPreferredSystemLanguages()`)
 *
 * Returns:
 *     str: One of the supported languages, DEFAULT_LANG when nothing
 *         matches
 */
export function matchSystemLanguage(locale: string, preferred: readonly string[] = []): string {
  const direct = matchLocale(locale);
  if (direct) {
    return direct;
  }

  for (const tag of preferred) {
    const match = matchLocale(tag);
    if (match) {
      return match;
    }
  }

  return DEFAULT_LANG;
}

/**
 * Chinese cannot be resolved by a base-language match: 'zh' is the base of
 * both Simplified and Traditional, so the first supported entry with that
 * base would always win and a Traditional Chinese user (Hong Kong, Macau,
 * Taiwan) would be served Simplified. The script or region subtag decides.
 */
const CHINESE_SCRIPTS: Record<string, string> = {
  hans: "zh-CN",
  hant: "zh-TW",
};

/** Regions that imply the script of a Chinese language tag. */
const CHINESE_REGIONS: Record<string, string> = {
  cn: "zh-CN",
  sg: "zh-CN",
  tw: "zh-TW",
  hk: "zh-TW",
  mo: "zh-TW",
};

/**
 * Resolve the Chinese variant a tag names, when the caller supports it.
 *
 * A bare 'zh' names no variant and is left to the base match, which answers
 * the first supported Chinese language (Simplified), the same way the backend
 * resolves it.
 *
 * @param subtags Subtags of the tag, base language first
 * @param supportedLanguages Languages the caller supports
 * @returns The supported Chinese language, undefined when the tag names none
 */
function matchChineseVariant(subtags: readonly string[], supportedLanguages: readonly string[]): string | undefined {
  for (const subtag of subtags) {
    const lang = CHINESE_SCRIPTS[subtag] ?? CHINESE_REGIONS[subtag];
    if (lang && supportedLanguages.includes(lang)) {
      return lang;
    }
  }
  return undefined;
}

/**
 * Matches user preferred languages against supported languages.
 *
 * Rules:
 * 1. Exact match: user 'en-US' -> matches 'en-US'.
 * 2. Chinese variant: user 'zh-HK' -> matches 'zh-TW' (see CHINESE_REGIONS).
 * 3. Base match: user 'en' -> matches 'en-US' (if 'en-US' starts with 'en').
 * 4. Fallback: returns defaultLang.
 *
 * @param userLanguages List from navigator.languages
 * @param supportedLanguages List of supported languages, in the generation order
 * @param defaultLang Fallback language
 */
export function matchLanguage(
  userLanguages: readonly string[],
  supportedLanguages: readonly string[],
  defaultLang: string,
): string {
  for (const userLang of userLanguages) {
    // 1. Exact Match
    if (supportedLanguages.includes(userLang)) {
      return userLang;
    }

    const subtags = userLang.toLowerCase().split("-");

    // 2. Chinese Variant Match
    // 'zh-HK' names Traditional Chinese, which the base match below cannot
    // tell apart from Simplified.
    if (subtags[0] === "zh") {
      const variant = matchChineseVariant(subtags.slice(1), supportedLanguages);
      if (variant) {
        return variant;
      }
    }

    // 3. Base Language Match (Smart promotion)
    // If user has 'en', and we support 'en-US', we match it.
    // Note: It matches the FIRST supported language with the same base.
    const userBase = subtags[0];
    const bestMatch = supportedLanguages.find((supported) => {
      const supportedBase = supported.split("-")[0];
      return supportedBase === userBase;
    });

    if (bestMatch) {
      return bestMatch;
    }
  }

  return defaultLang;
}

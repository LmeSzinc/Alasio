/**
 * Matches user preferred languages against supported languages.
 *
 * Rules:
 * 1. Exact match: user 'en-US' -> matches 'en-US'.
 * 2. Base match: user 'en' -> matches 'en-US' (if 'en-US' starts with 'en').
 * 3. Fallback: returns defaultLang.
 *
 * @param userLanguages List from navigator.languages
 * @param supportedLanguages List from config
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

    // 2. Base Language Match (Smart promotion)
    // If user has 'en', and we support 'en-US', we match it.
    // Note: It matches the FIRST supported language with the same base.
    const userBase = userLang.split("-")[0];
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

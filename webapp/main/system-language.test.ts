/**
 * Unit tests for the system language matching.
 */
import { describe, expect, it } from "vitest";
import { matchLocale, matchSystemLanguage } from "./system-language";

describe("TestMatchLocale", () => {
  it("matches an exact supported tag", () => {
    expect(matchLocale("en-US")).toBe("en-US");
    expect(matchLocale("zh-CN")).toBe("zh-CN");
    expect(matchLocale("ja-JP")).toBe("ja-JP");
    expect(matchLocale("zh-TW")).toBe("zh-TW");
    expect(matchLocale("es-ES")).toBe("es-ES");
  });

  it("ignores case and separator spelling", () => {
    expect(matchLocale("en-us")).toBe("en-US");
    expect(matchLocale("ZH-cn")).toBe("zh-CN");
    expect(matchLocale("ja_JP")).toBe("ja-JP");
    expect(matchLocale("es_ES.UTF-8")).toBe("es-ES");
  });

  it("strips the charset and modifier suffixes g_get_language_names adds", () => {
    expect(matchLocale("zh_CN.UTF-8")).toBe("zh-CN");
    expect(matchLocale("zh_CN.utf8")).toBe("zh-CN");
    expect(matchLocale("zh_TW.Big5")).toBe("zh-TW");
    expect(matchLocale("sr_RS@latin")).toBeUndefined();
  });

  it("resolves chinese variants by script subtag", () => {
    expect(matchLocale("zh-Hans")).toBe("zh-CN");
    expect(matchLocale("zh-Hans-CN")).toBe("zh-CN");
    expect(matchLocale("zh-Hans-SG")).toBe("zh-CN");
    expect(matchLocale("zh-Hant")).toBe("zh-TW");
    expect(matchLocale("zh-Hant-TW")).toBe("zh-TW");
    expect(matchLocale("zh-Hant-HK")).toBe("zh-TW");
  });

  it("resolves chinese variants by region subtag", () => {
    expect(matchLocale("zh-CN")).toBe("zh-CN");
    expect(matchLocale("zh-SG")).toBe("zh-CN");
    expect(matchLocale("zh-TW")).toBe("zh-TW");
    expect(matchLocale("zh-HK")).toBe("zh-TW");
    expect(matchLocale("zh-MO")).toBe("zh-TW");
  });

  it("falls back to simplified for a bare chinese tag", () => {
    expect(matchLocale("zh")).toBe("zh-CN");
  });

  it("does not read a region subtag as a chinese variant", () => {
    // 'hk' only means Traditional Chinese under the 'zh' base; en-HK is
    // English (Hong Kong) and must not be pulled to zh-TW
    expect(matchLocale("en-HK")).toBe("en-US");
    expect(matchLocale("en-SG")).toBe("en-US");
    expect(matchLocale("es-MO")).toBe("es-ES");
  });

  it("falls back to the base language", () => {
    expect(matchLocale("en-GB")).toBe("en-US");
    expect(matchLocale("en-AU")).toBe("en-US");
    expect(matchLocale("ja")).toBe("ja-JP");
    expect(matchLocale("es-419")).toBe("es-ES");
    expect(matchLocale("es-MX")).toBe("es-ES");
    // an unsupported region on top of a supported base still matches
    expect(matchLocale("ja-JP-mac")).toBe("ja-JP");
  });

  it("rejects unsupported languages", () => {
    expect(matchLocale("de-DE")).toBeUndefined();
    expect(matchLocale("fr-FR")).toBeUndefined();
    expect(matchLocale("ko-KR")).toBeUndefined();
    expect(matchLocale("ru")).toBeUndefined();
    expect(matchLocale("C")).toBeUndefined();
    expect(matchLocale("POSIX")).toBeUndefined();
  });

  it("rejects empty input", () => {
    expect(matchLocale("")).toBeUndefined();
    expect(matchLocale(".")).toBeUndefined();
    expect(matchLocale("_")).toBeUndefined();
    expect(matchLocale("@latin")).toBeUndefined();
  });
});

describe("TestMatchSystemLanguage", () => {
  it("uses the application locale when it matches", () => {
    expect(matchSystemLanguage("zh-CN", ["en-US", "zh-Hans-CN"])).toBe("zh-CN");
    expect(matchSystemLanguage("zh-Hant-HK", [])).toBe("zh-TW");
    expect(matchSystemLanguage("en-GB", [])).toBe("en-US");
  });

  it("does not let the preferred list override a matching locale", () => {
    // measured on Windows: the language list can begin with en-US while the
    // effective display language is zh-CN, so the locale must win
    expect(matchSystemLanguage("zh-CN", ["en-US", "zh-Hans-CN"])).toBe("zh-CN");
    expect(matchSystemLanguage("ja-JP", ["en-US"])).toBe("ja-JP");
  });

  it("falls back to the first supported preferred language", () => {
    expect(matchSystemLanguage("de-DE", ["de-DE", "zh-Hans-CN"])).toBe("zh-CN");
    expect(matchSystemLanguage("fr-FR", ["de-DE", "zh-Hant", "ja-JP"])).toBe("zh-TW");
    expect(matchSystemLanguage("C", ["de-DE", "es-ES"])).toBe("es-ES");
  });

  it("skips preferred entries the client does not support", () => {
    expect(matchSystemLanguage("ko-KR", ["ko-KR", "de-DE", "en-GB"])).toBe("en-US");
  });

  it("falls back to the default language", () => {
    expect(matchSystemLanguage("de-DE")).toBe("en-US");
    expect(matchSystemLanguage("de-DE", [])).toBe("en-US");
    expect(matchSystemLanguage("de-DE", ["fr-FR", "ko-KR"])).toBe("en-US");
    expect(matchSystemLanguage("", [""])).toBe("en-US");
  });
});

/**
 * Tests for the language negotiation of the frontend.
 *
 * The matcher serves the browser languages of a remote session (navigator
 * languages), where the caller supplies the supported list: the rules are the
 * exact tag, the Chinese variant, the base language, and the default.
 */
import { describe, expect, it } from "vitest";
import { matchLanguage } from "./negotiate";

/** The GUI languages the frontend ships, in the generation order. */
const SUPPORTED = ["en-US", "zh-CN", "ja-JP", "zh-TW", "es-ES"];
const DEFAULT = "en-US";

describe("TestMatchLanguageExact", () => {
  it("takes a supported tag as is", () => {
    for (const lang of SUPPORTED) {
      expect(matchLanguage([lang], SUPPORTED, DEFAULT)).toBe(lang);
    }
  });

  it("takes the first supported tag of the preference list", () => {
    expect(matchLanguage(["de-DE", "ja-JP", "en-US"], SUPPORTED, DEFAULT)).toBe("ja-JP");
  });

  it("keeps the exact tag of the supported list, not the browser spelling", () => {
    // the returned value is used as a translation key
    expect(matchLanguage(["zh-TW"], SUPPORTED, DEFAULT)).toBe("zh-TW");
  });
});

describe("TestMatchLanguageChinese", () => {
  it("resolves the traditional regions to traditional chinese", () => {
    // 'zh' is the base of both scripts: without this step the base match
    // answers zh-CN (the first supported Chinese language) for all of them
    expect(matchLanguage(["zh-HK", "zh", "en-US", "en"], SUPPORTED, DEFAULT)).toBe("zh-TW");
    expect(matchLanguage(["zh-MO", "zh", "en-US"], SUPPORTED, DEFAULT)).toBe("zh-TW");
    expect(matchLanguage(["zh-Hant-HK", "zh-Hant", "zh"], SUPPORTED, DEFAULT)).toBe("zh-TW");
    expect(matchLanguage(["zh-Hant"], SUPPORTED, DEFAULT)).toBe("zh-TW");
  });

  it("resolves the simplified regions to simplified chinese", () => {
    expect(matchLanguage(["zh-CN", "zh", "en-US"], SUPPORTED, DEFAULT)).toBe("zh-CN");
    expect(matchLanguage(["zh-SG", "zh", "en-US"], SUPPORTED, DEFAULT)).toBe("zh-CN");
    expect(matchLanguage(["zh-Hans-SG", "zh"], SUPPORTED, DEFAULT)).toBe("zh-CN");
    expect(matchLanguage(["zh-Hans-HK", "zh"], SUPPORTED, DEFAULT)).toBe("zh-CN");
  });

  it("resolves the region on its own, without a trailing base tag", () => {
    // a browser language list degrades towards the base code, which would hide
    // a missing region rule; the region must decide by itself
    expect(matchLanguage(["zh-SG"], SUPPORTED, DEFAULT)).toBe("zh-CN");
    expect(matchLanguage(["zh-HK"], SUPPORTED, DEFAULT)).toBe("zh-TW");
    expect(matchLanguage(["zh-MO"], SUPPORTED, DEFAULT)).toBe("zh-TW");
    expect(matchLanguage(["zh-Hant-MO"], SUPPORTED, DEFAULT)).toBe("zh-TW");
  });

  it("reads the script before the region", () => {
    // an explicit script wins over a region that implies the other one
    expect(matchLanguage(["zh-Hant-CN"], SUPPORTED, DEFAULT)).toBe("zh-TW");
    expect(matchLanguage(["zh-Hans-TW"], SUPPORTED, DEFAULT)).toBe("zh-CN");
  });

  it("answers the default for a bare chinese tag", () => {
    // no variant named: the base match picks the first supported Chinese
    // language, which is the backend behaviour for 'zh' as well
    expect(matchLanguage(["zh"], SUPPORTED, DEFAULT)).toBe("zh-CN");
  });

  it("falls through when the variant is not supported", () => {
    // only Simplified is offered: the variant step finds nothing and the base
    // match answers Simplified, which is still readable to the user
    const onlySimplified = ["en-US", "zh-CN"];
    expect(matchLanguage(["zh-HK", "zh"], onlySimplified, DEFAULT)).toBe("zh-CN");
  });
});

describe("TestMatchLanguageBase", () => {
  it("promotes a bare base language to the supported tag", () => {
    expect(matchLanguage(["en"], SUPPORTED, DEFAULT)).toBe("en-US");
    expect(matchLanguage(["ja"], SUPPORTED, DEFAULT)).toBe("ja-JP");
    expect(matchLanguage(["es"], SUPPORTED, DEFAULT)).toBe("es-ES");
  });

  it("promotes an unsupported region to the same base", () => {
    expect(matchLanguage(["en-GB", "en"], SUPPORTED, DEFAULT)).toBe("en-US");
    expect(matchLanguage(["es-MX", "es"], SUPPORTED, DEFAULT)).toBe("es-ES");
    expect(matchLanguage(["ja-JP-mac", "ja"], SUPPORTED, DEFAULT)).toBe("ja-JP");
  });

  it("does not read a region as a chinese variant of another language", () => {
    // 'hk' only implies Traditional Chinese under the 'zh' base
    expect(matchLanguage(["en-HK", "en"], SUPPORTED, DEFAULT)).toBe("en-US");
    expect(matchLanguage(["es-MO", "es"], SUPPORTED, DEFAULT)).toBe("es-ES");
  });
});

describe("TestMatchLanguageFallback", () => {
  it("answers the default when nothing matches", () => {
    expect(matchLanguage(["de-DE", "de"], SUPPORTED, DEFAULT)).toBe("en-US");
    expect(matchLanguage(["ko-KR"], SUPPORTED, DEFAULT)).toBe("en-US");
    expect(matchLanguage([], SUPPORTED, DEFAULT)).toBe("en-US");
    expect(matchLanguage([""], SUPPORTED, DEFAULT)).toBe("en-US");
  });

  it("answers the default the caller passes", () => {
    expect(matchLanguage(["ko-KR"], SUPPORTED, "ja-JP")).toBe("ja-JP");
  });

  it("answers the default for an empty supported list", () => {
    expect(matchLanguage(["en-US"], [], "es-ES")).toBe("es-ES");
  });
});

/**
 * Tests for the embedded-session language bootstrap.
 *
 * The state module reads the cookie and the iframe URL once, at import time,
 * so every case re-evaluates it behind vi.resetModules() after preparing the
 * jsdom URL and cookie.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Import a fresh instance of the i18n state module.
 *
 * Args:
 *     search (str): Query string the module sees in `location.search`
 *     cookie (str): Value of the `alasio_lang` cookie, omitted for none
 *
 * Returns:
 *     Promise[object]: The freshly evaluated module
 */
async function loadState(search: string, cookie?: string) {
  window.history.replaceState({}, "", `/${search}`);
  document.cookie = "alasio_lang=; path=/; max-age=0";
  if (cookie !== undefined) {
    document.cookie = `alasio_lang=${cookie}; path=/`;
  }
  vi.resetModules();
  return await import("./state.svelte");
}

beforeEach(() => {
  document.cookie = "alasio_lang=; path=/; max-age=0";
});

describe("TestEmbeddedUrlLang", () => {
  it("applies the display language the host passes in the iframe URL", async () => {
    const state = await loadState("?embedded=electron&lang=ja-JP&langConfig=ja-JP");
    expect(state.i18nState.l).toBe("ja-JP");
    expect(state.i18nState.configLang).toBe("ja-JP");
  });

  it("keeps the host config value when it follows the system", async () => {
    // The host derived zh-TW from its OS language while its config value
    // stays 'system': the display value is concrete, the config value is not
    const state = await loadState("?embedded=electron&lang=zh-TW&langConfig=system");
    expect(state.i18nState.l).toBe("zh-TW");
    expect(state.i18nState.configLang).toBe("system");
  });

  it("overrides a cookie that disagrees with the host", async () => {
    // The host is the single source of truth: a stale per-client cookie
    // must not survive the embedded first paint
    const state = await loadState("?embedded=electron&lang=zh-CN&langConfig=zh-CN", "es-ES");
    expect(state.i18nState.l).toBe("zh-CN");
    expect(state.i18nState.configLang).toBe("zh-CN");
    expect(document.cookie).toContain("alasio_lang=zh-CN");
  });

  it("keeps the cookie config when the URL carries no langConfig", async () => {
    const state = await loadState("?embedded=electron&lang=ja-JP", "system");
    expect(state.i18nState.l).toBe("ja-JP");
    expect(state.i18nState.configLang).toBe("system");
  });

  it("ignores an unsupported language", async () => {
    const state = await loadState("?embedded=electron&lang=de-DE&langConfig=de-DE");
    // Falls back to the browser guess (jsdom reports en-US)
    expect(state.i18nState.l).toBe("en-US");
    expect(state.i18nState.configLang).toBe("system");
  });

  it("ignores an unsupported config value but keeps the display language", async () => {
    const state = await loadState("?embedded=electron&lang=ja-JP&langConfig=de-DE");
    expect(state.i18nState.l).toBe("ja-JP");
    expect(state.i18nState.configLang).toBe("system");
  });

  it("ignores the language pair outside an embedded session", async () => {
    // A remote visitor editing the query must not change the language: the
    // pair is part of the ?embedded=electron host contract only
    const state = await loadState("?lang=ja-JP&langConfig=ja-JP");
    expect(state.i18nState.l).toBe("en-US");
    expect(state.i18nState.configLang).toBe("system");
  });

  it("keeps the cookie language outside an embedded session", async () => {
    const state = await loadState("?lang=ja-JP", "es-ES");
    expect(state.i18nState.l).toBe("es-ES");
    expect(state.i18nState.configLang).toBe("es-ES");
  });

  it("derives the display language from a 'system' cookie", async () => {
    const state = await loadState("?embedded=electron", "system");
    expect(state.i18nState.l).toBe("en-US");
    expect(state.i18nState.configLang).toBe("system");
  });
});

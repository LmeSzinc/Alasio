import path from "path";

export interface I18nConfig {
  /** Root directory of source code */
  srcPath: string;
  /** Directory where JSON translation files are stored */
  i18nPath: string;
  /** Directory where generated TS files will be output */
  genPath: string;
  /** List of supported languages (e.g., ['en-US', 'zh-CN']) */
  languages: string[];
  /** Current working directory */
  cwd: string;
  /**
   * Generation mode:
   * - 'svelte': generated modules read `i18nState.l` from a svelte runes state
   * - 'node': generated modules read `getLang()` from a plain ts state module
   */
  mode: "svelte" | "node";
  /**
   * Module to import for reading the current language,
   * e.g. '$lib/i18n/state.svelte' (svelte) or './state' (node)
   */
  stateModule: string;
}

const LANGUAGES = ["en-US", "zh-CN", "ja-JP", "zh-TW", "es-ES"];

/** Renderer (svelte mode) config: translations used by renderer/src */
export const rendererI18nConfig: I18nConfig = {
  cwd: process.cwd(),
  srcPath: "renderer/src",
  i18nPath: "renderer/src/i18n",
  genPath: "renderer/src/i18ngen",
  // Convention: Always use xx-YY format.
  // The first language is considered the default/fallback.
  languages: LANGUAGES,
  mode: "svelte",
  stateModule: "$lib/i18n/state.svelte",
};

/** Main (node mode) config: translations used by the electron main process */
export const mainI18nConfig: I18nConfig = {
  cwd: process.cwd(),
  srcPath: "main",
  i18nPath: "main/i18n",
  genPath: "main/i18ngen",
  languages: LANGUAGES,
  mode: "node",
  stateModule: "./state",
};

/** Helper to resolve absolute paths based on config */
export const resolvePath = (config: I18nConfig, ...args: string[]) =>
  path.resolve(config.cwd, ...args).replace(/\\/g, "/");

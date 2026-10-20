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
}

export const i18nConfig: I18nConfig = {
  cwd: process.cwd(),
  srcPath: "src",
  i18nPath: "src/i18n",
  genPath: "src/i18ngen",
  // Convention: Always use xx-YY format.
  // The first language is considered the default/fallback.
  languages: ["en-US", "zh-CN", "ja-JP", "zh-TW", "es-ES"],
  mode: "svelte",
};

/** Helper to resolve absolute paths based on config */
export const resolvePath = (...args: string[]) => path.resolve(i18nConfig.cwd, ...args).replace(/\\/g, "/");

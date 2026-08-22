import { app, nativeTheme } from "electron";
import { sendStdinCommand } from "./backend";
import { DEFAULT_LANG, SUPPORTED_LANGS } from "./i18ngen";
import type { ConfigError } from "./config";

// Host-level language values: 'system' or one of the supported languages.
// The webapp main process is the single source of truth; the backend only
// persists the value into deploy.yaml (Webapp.Lang) through the stdin
// contract.
export const CONFIG_LANGS = [...SUPPORTED_LANGS, "system"] as const;
export type ConfigLang = (typeof CONFIG_LANGS)[number];

// Host-level theme values: 'system', 'light' or 'dark'
export const CONFIG_THEMES = ["system", "light", "dark"] as const;
export type ConfigTheme = (typeof CONFIG_THEMES)[number];

export function isConfigLang(value: string): value is ConfigLang {
  return (CONFIG_LANGS as readonly string[]).includes(value);
}

export function isConfigTheme(value: string): value is ConfigTheme {
  return (CONFIG_THEMES as readonly string[]).includes(value);
}

/**
 * Match the OS locale against the supported languages: exact match first,
 * then base-language prefix match, then the default language.
 */
function matchSystemLanguage(): string {
  const locale = app.getLocale();
  if ((SUPPORTED_LANGS as readonly string[]).includes(locale)) {
    return locale;
  }
  const base = locale.split("-")[0];
  const match = (SUPPORTED_LANGS as readonly string[]).find((l) => l.split("-")[0] === base);
  return match || DEFAULT_LANG;
}

class AppState {
  // === Startup config (set by config.ts loadConfig) ===
  pythonExecutable = "";
  rootPath = "";
  backendHost = "0.0.0.0";
  backendPort = 22267;
  isFirstTimeSetup = false;
  templatePath?: string;
  deployPath?: string;
  configError?: ConfigError;

  // === Runtime state ===
  // Persistent values (single source of truth, persisted by the backend
  // into deploy.yaml through the stdin contract)
  configLang: ConfigLang = "system";
  configTheme: ConfigTheme = "system";
  // Derived display values (always concrete)
  displayLang: string = DEFAULT_LANG;
  displayTheme: "light" | "dark" = "light";

  private listeners = new Set<() => void>();

  /**
   * Called once after app ready: registers the nativeTheme listener and
   * derives the initial display values. nativeTheme must not be touched
   * before app ready.
   */
  init(): void {
    nativeTheme.on("updated", () => {
      if (this.configTheme === "system") {
        this.deriveDisplay();
        this.notify();
      }
    });
    this.deriveDisplay();
  }

  /** Register a change listener, returns an unsubscribe function. */
  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Set the persistent language ('system' or one of the supported
   * languages). Derives the display value, broadcasts to the backend
   * through stdin, notifies listeners.
   *
   * While the backend is offline a change is simply lost: the frontend
   * (served by the backend) is unreachable too, so there is no user entry
   * to change language in that state. The yaml remains the only
   * persistence.
   */
  setLang(configLang: string): void {
    if (!isConfigLang(configLang)) return;
    if (configLang === this.configLang) return;
    this.configLang = configLang;
    this.applyAndBroadcast();
  }

  /**
   * Set the persistent theme ('system' | 'light' | 'dark'). Same flow as
   * setLang.
   */
  setTheme(configTheme: string): void {
    if (!isConfigTheme(configTheme)) return;
    if (configTheme === this.configTheme) return;
    this.configTheme = configTheme;
    this.applyAndBroadcast();
  }

  /**
   * Push the current config values to the backend through the stdin
   * contract (command:set_lang:{configLang} + command:set_theme:{configTheme}).
   * Called after the backend is ready and after every change; the backend
   * persists idempotently (no write when the value already matches).
   */
  broadcastPrefs(): void {
    sendStdinCommand(`command:set_lang:${this.configLang}`);
    sendStdinCommand(`command:set_theme:${this.configTheme}`);
  }

  private applyAndBroadcast(): void {
    this.deriveDisplay();
    this.broadcastPrefs();
    this.notify();
  }

  private deriveDisplay(): void {
    this.displayLang = this.configLang === "system" ? matchSystemLanguage() : this.configLang;
    this.displayTheme =
      this.configTheme === "system" ? (nativeTheme.shouldUseDarkColors ? "dark" : "light") : this.configTheme;
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}

// Module-level singleton, created early in the process startup (before
// createWindow). loadConfig() populates the startup config and the
// persistent language/theme values.
export const appState = new AppState();

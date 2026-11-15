import { app, nativeTheme } from "electron";
import { sendStdinCommand } from "./backend";
import type { ConfigError } from "./config";
import { DEFAULT_LANG, SUPPORTED_LANGS } from "./i18ngen";
import { matchSystemLanguage } from "./system-language";

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
  // Host-level dpi scaling: a single value (true = follow the system DPI
  // scaling, false = force scale factor 1), no config/display split. It
  // only takes effect through the electron startup command-line switch
  // (force-device-scale-factor), so changes apply on the next launch.
  dpiScaling = true;
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
   * Set the persistent dpi scaling (true = follow the system DPI scaling,
   * false = force scale factor 1). Unlike language/theme there is a
   * single value, no derived display value. Same flow as setLang/setTheme:
   * no-op on identical value, broadcast to the backend, notify listeners.
   */
  setDpiScaling(dpiScaling: boolean): void {
    if (typeof dpiScaling !== "boolean") return;
    if (dpiScaling === this.dpiScaling) return;
    this.dpiScaling = dpiScaling;
    this.broadcastPrefs();
    this.notify();
  }

  /**
   * Push the current config values to the backend through the stdin
   * contract (command:set_lang:{configLang} + command:set_theme:{configTheme}
   * + command:set_dpi_scaling:{dpiScaling}). Called after the backend is
   * ready and after every change; the backend persists idempotently (no
   * write when the value already matches).
   */
  broadcastPrefs(): void {
    sendStdinCommand(`command:set_lang:${this.configLang}`);
    sendStdinCommand(`command:set_theme:${this.configTheme}`);
    sendStdinCommand(`command:set_dpi_scaling:${this.dpiScaling}`);
  }

  private applyAndBroadcast(): void {
    this.deriveDisplay();
    this.broadcastPrefs();
    this.notify();
  }

  /**
   * Derive the concrete display values from the config values.
   *
   * The display language comes from the application locale, which is the
   * OS display language; see matchSystemLanguage() for why the regional
   * locale is not used as the primary source.
   */
  private deriveDisplay(): void {
    this.displayLang =
      this.configLang === "system"
        ? matchSystemLanguage(app.getLocale(), app.getPreferredSystemLanguages())
        : this.configLang;

    // Drive Chromium's own light/dark state as well. The renderer paints
    // itself from the display theme (the `.dark` class + colorScheme), but the
    // native parts Chromium renders (devtools, context menus on Linux/Windows)
    // and `prefers-color-scheme` inside the renderer follow `themeSource`.
    // Without this the app could be dark while the embedded page still reports
    // the OS preference. Assigning 'system' restores the OS default, so a
    // config value of 'system' needs no special case.
    //
    // Order matters when the config is 'system': themeSource must be settled
    // first, otherwise shouldUseDarkColors below would answer for the previous
    // override. The 'updated' listener is filtered on configTheme, so this
    // assignment cannot feed back into deriveDisplay while the override is a
    // concrete value.
    nativeTheme.themeSource = this.configTheme;
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

import { type Plugin } from "vite";
import { i18nConfig, resolvePath } from "./config";
import { I18nGenerator } from "./core";

// Singleton instance to maintain cache state during dev server runtime
export const generator = new I18nGenerator(i18nConfig);

export function i18nPlugin(): Plugin {
  return {
    name: "vite-plugin-svelte-i18n",

    // Run full scan on server start
    async buildStart() {
      await generator.init();
    },

    // Handle HMR
    async handleHotUpdate({ file }) {
      const absSrc = resolvePath(i18nConfig.srcPath);
      const absGen = resolvePath(i18nConfig.genPath);
      const absI18n = resolvePath(i18nConfig.i18nPath);

      // 1. Translation JSON changed
      if (file.startsWith(absI18n) && file.endsWith(".json")) {
        await generator.handleJsonUpdate(file);
        // No reload on json changes since i18n json will be baked into ts file
        return [];
      }

      // 2. Source code changed (ignore generated files)
      if (
        (file.endsWith(".svelte") || file.endsWith(".ts") || file.endsWith(".js")) &&
        file.startsWith(absSrc) &&
        !file.startsWith(absGen)
      ) {
        await generator.handleSourceUpdate(file);
      }
    },
  };
}

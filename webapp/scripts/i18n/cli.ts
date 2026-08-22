// Run with: tsx scripts/i18n/cli.ts
import { I18nGenerator } from "./core.ts";
import { mainI18nConfig, rendererI18nConfig } from "./config.ts";

const cliGenerators = [rendererI18nConfig, mainI18nConfig].map((config) => new I18nGenerator(config));

console.log("[CLI] Starting I18n Generation...");
Promise.all(cliGenerators.map((generator) => generator.init()))
  .then(() => {
    console.log("[CLI] Complete.");
    process.exit(0);
  })
  .catch((err) => {
    console.error("[CLI] Error:", err);
    process.exit(1);
  });

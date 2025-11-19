// Run with: tsx scripts/i18n/cli.ts
import { I18nGenerator } from "./core";
import { i18nConfig } from "./config";

const cliGenerator = new I18nGenerator(i18nConfig);

console.log("[CLI] Starting I18n Generation...");
cliGenerator
  .init()
  .then(() => {
    console.log("[CLI] Complete.");
    process.exit(0);
  })
  .catch((err) => {
    console.error("[CLI] Error:", err);
    process.exit(1);
  });

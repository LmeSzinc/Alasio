// Import the generated entry point.
// NOTE: This file might be empty or missing on the very first run before script initialization.
// The build script's `createEmptyEntry` ensures it exists to prevent build errors.
// @ts-ignore
import * as Generated from "../../i18ngen/index";

// === FALLBACK LOGIC ===

/**
 * Returns a fallback string when translation is missing.
 * Helps prevent crashes during development or if generated code is out of sync.
 */
function fallback(moduleName: string, keyName: string, args?: any) {
  if (import.meta.env.DEV) {
    // Optional: Log a debug message
    // console.debug(`[i18n] Fallback for ${moduleName}.${keyName}`);
  }
  // Return the key itself as the text
  return keyName;
}

// === PROXY HANDLER ===

const proxyHandler = {
  get(target: any, moduleName: string) {
    // 1. Get the generated module (e.g., Home)
    const generatedModule = target[moduleName];

    // 2. Return a second Proxy for the Key access (e.g., Home.Hello)
    return new Proxy(
      {},
      {
        get(_, keyName: string) {
          return (...args: any[]) => {
            // 3. Try to execute the generated function
            if (generatedModule && typeof generatedModule[keyName] === "function") {
              try {
                return generatedModule[keyName](args[0]);
              } catch (e) {
                console.error(`[i18n] Error executing ${moduleName}.${keyName}`, e);
                return fallback(moduleName, keyName, args[0]);
              }
            }

            // 4. Fallback if module or key doesn't exist
            return fallback(moduleName, keyName, args[0]);
          };
        },
      },
    );
  },
};

// === EXPORT ===

// Use the generated 't' object as the target.
// If Generated is undefined (extreme edge case), use an empty object.
// @ts-ignore
const realT = Generated?.t || {};

/**
 * The safe 't' object.
 * It proxies calls to the generated static code, but falls back to returning the key name
 * if the code hasn't been generated yet.
 */
export const t = new Proxy(realT, proxyHandler) as typeof Generated.t;

// Export state management
export { i18nState, setLang } from "./state.svelte";


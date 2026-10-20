import { defineConfig } from "vitest/config";

/**
 * Vitest configuration for the electron main / preload process.
 *
 * Kept separate from vite.config.mts on purpose: that config boots the
 * SvelteKit + electron + tailwind + i18n plugin stack, which rewrites
 * `.svelte-kit/package.json` and rescans the sources, none of which a
 * node-side unit test needs. Vitest resolves `vitest.config.*` before
 * `vite.config.*`, so the build config never loads here.
 *
 * Tests run in the node environment: they cover the pure logic of the
 * main process (log buffering, line splitting, startup arguments, token
 * chaining) and must not start electron. Renderer tests would need the
 * svelte plugin and a jsdom environment, which are not part of this
 * project yet; add them here together with the first renderer test.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["main/**/*.test.ts", "preload/**/*.test.ts", "shared/**/*.test.ts"],
  },
});

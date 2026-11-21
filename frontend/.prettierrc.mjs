// Prettier configuration.
//
// This is a .mjs file rather than .prettierrc because the two sorts of a
// .svelte file have to be composed by hand. prettier-plugin-tailwindcss 0.8
// owns the `svelte` parser (that is how it rewrites the class lists) and it has
// a compatibility hook for the sort-imports plugins, but that hook loads the
// compatible plugin by package name and reads a `parsers` export that
// @trivago/prettier-plugin-sort-imports (an ESM package with a default export)
// does not expose, so the imports of .svelte files would silently stay
// unsorted. Instead the plugin below takes tailwind's parser, which carries the
// class rewrite, and runs the sort-imports preprocessor first, so a .svelte
// file is formatted with both: imports sorted, then classes sorted on the
// parsed AST.
import sortImports from "@trivago/prettier-plugin-sort-imports";
import * as sveltePlugin from "prettier-plugin-svelte";
import * as tailwindPlugin from "prettier-plugin-tailwindcss";

/** The svelte parser of the sort-imports plugin: sorts the imports, then runs the svelte preprocess. */
const sortSveltePreprocess = sortImports.parsers?.svelte?.preprocess;

// Captured before prettier resolves the lazy parsers of the plugins: it
// replaces the factory on the plugin object with the parser it produced.
const tailwindSvelteParser = tailwindPlugin.parsers?.svelte;

/**
 * The svelte parser as prettier sees it: tailwind's parser (class sorting)
 * with the imports sorted before it parses.
 */
const svelteParserWithBothSorts = {
  parsers: {
    // A lazy parser like the one of the tailwind plugin itself: prettier awaits
    // the factory, and the wrapper parser is built once the module is loaded.
    svelte: async () => {
      const parser =
        typeof tailwindSvelteParser === "function" ? await tailwindSvelteParser() : tailwindSvelteParser;
      return {
        ...parser,
        preprocess: async (text, options) => {
          // The preprocessor of the sort-imports plugin already runs the svelte
          // preprocessor afterwards, so it replaces the original one instead of
          // wrapping it (running both would preprocess the file twice).
          if (sortSveltePreprocess) return sortSveltePreprocess(text, options);
          return parser.preprocess ? parser.preprocess(text, options) : text;
        },
      };
    },
  },
  // The printer of the tailwind plugin is the one that also rewrites the class
  // strings of the svelte AST, keep it for the parser above.
  printers: tailwindPlugin.printers,
};

/** @type {import("prettier").Config} */
export default {
  useTabs: false,
  singleQuote: false,
  printWidth: 120,
  // The tailwind plugin must stay last, and the composed parser after it so it
  // is the one prettier picks for .svelte files.
  plugins: [sveltePlugin, sortImports, tailwindPlugin, svelteParserWithBothSorts],
  overrides: [
    {
      files: "*.svelte",
      options: {
        parser: "svelte",
      },
    },
  ],
  importOrder: [
    "<BUILTIN_MODULES>",
    "<THIRD_PARTY_MODULES>",
    "^@lucide/svelte/(.*)$",
    "^\\$(lib|src|app|private|routes|public)/(.*)$",
    "^[./]",
  ],
  importOrderSortSpecifiers: true,
  // Tailwind 4 has no JS config: the theme and the custom utilities live in
  // the stylesheet, and without it the plugin can not sort the class lists.
  tailwindStylesheet: "./src/app.css",
  // Class lists passed to the class-merging helpers are sorted too: cn() is
  // how the components compose their Tailwind classes.
  tailwindFunctions: ["cn", "clsx"],
};

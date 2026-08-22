import fs from "fs-extra";
import path from "path";
import glob from "fast-glob";
import { DROPPED_SUFFIX } from "../svelte-drop-dev-page/files.ts";
import { type I18nConfig, resolvePath } from "./config.ts";

// Matches usage like: t.Home.Hello(
// Capture Group 1: Module Name (e.g., Home)
// Capture Group 2: Key Name (e.g., Hello)
const CALL_REGEX = /\bt\.([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\(/g;

// Matches usage like: {name} inside translation text
const ARG_REGEX = /\{(\w+)\}/g;

const toVar = (lang: string) => `L_${lang.replace(/[^a-zA-Z0-9]/g, "_")}`;

export class I18nGenerator {
  private config: I18nConfig;
  // Memory Cache: FilePath -> { ModuleName -> Set<Key> }
  private fileCache = new Map<string, Record<string, Set<string>>>();
  // Fingerprint to detect if module list changed (to avoid regenerating entry files)
  private lastModuleSignature = "";

  constructor(config: I18nConfig) {
    this.config = config;
  }

  // === 1. Public API ===

  /**
   * Full initialization: scans all files and generates everything.
   * Includes a safe-guard to create empty entry files before Vite starts.
   */
  async init() {
    const start = Date.now();
    console.log(`[i18n] Initializing (${this.config.mode} mode)...`);

    // Ensure directories exist
    await fs.ensureDir(resolvePath(this.config, this.config.i18nPath));
    await fs.ensureDir(resolvePath(this.config, this.config.genPath));

    // === SAFEGUARD: Create empty entry files if they don't exist ===
    // This prevents Vite from crashing with "Module not found" on first run.
    if (!fs.existsSync(resolvePath(this.config, this.config.genPath, "index.ts"))) {
      await this.createEmptyEntry();
    }

    // Also scan files with the svelte-drop-dev-page temporary suffix
    // (e.g. `+page.svelte.dropped`). They are route files temporarily
    // renamed out of sveltekit's sight during a build; an interrupted
    // build may leave them behind, so stale-key cleanup must still see
    // their i18n usage or their translations would be dropped.
    const files = await glob(
      [
        `${this.config.srcPath}/**/*.{svelte,ts,js}`,
        `${this.config.srcPath}/**/*.svelte${DROPPED_SUFFIX}`,
        `${this.config.srcPath}/**/*.ts${DROPPED_SUFFIX}`,
      ],
      {
        cwd: this.config.cwd,
        absolute: true,
        ignore: [
          // Ignore generated files
          this.config.genPath,
          // Optional: Ignore tests
          "**/*.test.ts",
          "**/*.test.js",
          "**/*.spec.ts",
          "**/*.spec.js",
        ],
      },
    );
    // fast-glob traverses the tree with parallel reads, so its result
    // order varies between runs under filesystem activity (e.g. a running
    // dev server). Sort by path so the generated module/key ordering is
    // deterministic across runs and platforms.
    files.sort();

    // Read all files in parallel, but apply the scan results in the order
    // files were returned by glob. Applying them in completion order would
    // make the cache insertion order — and therefore the generated
    // module/key ordering — nondeterministic across runs.
    const contents = await Promise.all(
      files.map(async (file) => {
        try {
          return await fs.readFile(file, "utf-8");
        } catch {
          return null;
        }
      }),
    );
    for (const [index, file] of files.entries()) {
      const content = contents[index];
      if (content !== null) this.applyScannedContent(file, content, false);
    }
    await this.reconcileAll();

    console.log(`[i18n] Initialization complete in ${Date.now() - start}ms`);
  }

  /**
   * Creates dummy entry files so the app can boot up.
   */
  private async createEmptyEntry() {
    const constPath = resolvePath(this.config, this.config.genPath, "constants.ts");
    const indexPath = resolvePath(this.config, this.config.genPath, "index.ts");

    // Generate constants
    const langVars = this.config.languages.map((l) => `export const ${toVar(l)} = "${l}";`);
    const constContent = [
      ...langVars,
      `export const SUPPORTED_LANGS = [${this.config.languages.map((l) => `"${l}"`).join(", ")}] as const;`,
      `export const DEFAULT_LANG = "${this.config.languages[0]}";`,
      "",
    ].join("\n");
    await fs.outputFile(constPath, constContent);

    // Generate empty t object
    const indexContent = [
      `export * from "./constants";`,
      // Node mode generated modules import './state', so it must exist too
      ...(this.config.mode === "node" ? [`export { setLang, getLang } from "./state";`] : []),
      `export const t = {};`, // Proxy in runtime will handle this empty object
      "",
    ].join("\n");
    await fs.outputFile(indexPath, indexContent);

    // Node mode: generate the plain language state module
    if (this.config.mode === "node") {
      await fs.outputFile(resolvePath(this.config, this.config.genPath, "state.ts"), this.stateModuleContent());
    }
  }

  /**
   * Content of the node-mode language state module (state.ts).
   * Generated modules read the current language through getLang().
   */
  private stateModuleContent() {
    return [
      `// Auto-generated language state (node mode)`,
      `import { DEFAULT_LANG } from "./constants";`,
      ``,
      `let currentLang: string = DEFAULT_LANG;`,
      ``,
      `export function setLang(lang: string) {`,
      `  currentLang = lang;`,
      `}`,
      ``,
      `export function getLang(): string {`,
      `  return currentLang;`,
      `}`,
      "",
    ].join("\n");
  }

  async handleSourceUpdate(filePath: string) {
    console.log(`[i18n] Source update detected: ${filePath}`);
    const affected = await this.scanFile(filePath);
    if (affected && affected.size > 0) {
      await this.updateModules(Array.from(affected));
      await this.runPipeline();
    }
  }

  /**
   * Handle updates in JSON files.
   * Only regenerates artifacts for the specific module.
   */
  async handleJsonUpdate(filePath: string) {
    const modName = path.basename(filePath, ".json");
    console.log(`[i18n] JSON update detected: ${modName}`);
    await this.generateModuleArtifacts(modName);
  }

  private async runPipeline() {
    const activeModules = this.getAllActiveModules();
    const diskModules = await this.getDiskModules();

    // 1. Garbage Collection
    const toRemove = diskModules.filter((m) => !activeModules.has(m));
    if (toRemove.length > 0) {
      console.log(`[i18n] GC Removing unused modules: ${toRemove.join(", ")}`);
      await Promise.all(toRemove.map((m) => this.removeModule(m)));
    }

    // 2. Check Fingerprint
    const signature = Array.from(activeModules).sort().join("|");
    if (signature !== this.lastModuleSignature) {
      console.log("[i18n] Module list changed, updating entry files.");
      await this.updateEntryFiles(activeModules);
      this.lastModuleSignature = signature;
    }
  }

  // === 3. Atomic Operations ===

  /**
   * Scans a single file for `t.Module.Key()` usages.
   * Returns a set of affected module names if usage changed.
   */
  private async scanFile(filePath: string, returnAffected = true): Promise<Set<string> | null> {
    try {
      const content = await fs.readFile(filePath, "utf-8");
      return this.applyScannedContent(filePath, content, returnAffected);
    } catch (e) {
      // Read failure: the file may be temporarily renamed (e.g. by
      // svelte-drop-dev-page during a concurrent build, or an editor's
      // atomic save). Treat it as a transient state: keep the cached
      // usage and report no change, so stale-key cleanup never deletes
      // translations based on a file we could not read.
      return null;
    }
  }

  /**
   * Applies the i18n usage of one file's content to the cache.
   *
   * The cache insertion order decides the generated module/key ordering
   * (first occurrence in source wins), so when scanning multiple files
   * this must be called in the order files were returned by glob — never
   * in the completion order of parallel reads.
   *
   * Returns a set of affected module names if usage changed.
   */
  private applyScannedContent(filePath: string, content: string, returnAffected = true): Set<string> | null {
    const modules: Record<string, Set<string>> = {};

    let match;
    while ((match = CALL_REGEX.exec(content)) !== null) {
      const [_, mod, key] = match;
      if (!modules[mod]) modules[mod] = new Set();
      modules[mod].add(key);
    }

    // Diff against cache
    const old = this.fileCache.get(filePath) || {};
    let changed = false;
    const affected = new Set<string>();
    const checkChange = (m: string) => {
      changed = true;
      affected.add(m);
    };

    // Check new/modified
    for (const mod in modules) {
      const newK = modules[mod];
      const oldK = old[mod];
      if (!oldK || !this.areSetsEqual(newK, oldK)) checkChange(mod);
    }
    for (const mod in old) if (!modules[mod]) checkChange(mod);
    if (changed) this.fileCache.set(filePath, modules);
    return returnAffected ? affected : null;
  }

  /**
   * Re-syncs JSON and generates TS for specific modules.
   */
  private async updateModules(names: string[]) {
    await Promise.all(names.map((mod) => this.processModule(mod)));
  }

  private async processModule(mod: string) {
    const allKeys = new Set<string>();
    for (const [_, mods] of this.fileCache) {
      if (mods[mod]) mods[mod].forEach((k) => allKeys.add(k));
    }

    // If keys are empty, GC will handle removal later in pipeline.
    // Only generate if there are keys.
    if (allKeys.size > 0) await this.syncJsonAndGen(mod, allKeys);
  }

  /**
   * Logic: Read JSON -> Keep Scanned Keys -> Write JSON -> Generate TS
   */
  private async syncJsonAndGen(mod: string, keys: Set<string>) {
    const jsonPath = resolvePath(this.config, this.config.i18nPath, `${mod}.json`);

    // Rebuild the JSON from the scanned keys only. Keys that are no
    // longer referenced by any source file are dropped, while the
    // translations of keys that still exist are preserved. The full scan
    // always covers every logical source file (including files under the
    // svelte-drop-dev-page temporary suffix), so a missing key means it
    // is genuinely unused and safe to remove.
    let currentContent = "";
    let currentOnDisk: Record<string, Record<string, string>> = {};
    try {
      currentContent = await fs.readFile(jsonPath, "utf-8");
      currentOnDisk = JSON.parse(currentContent);
    } catch {}

    const removed = Object.keys(currentOnDisk).filter((k) => !keys.has(k));
    if (removed.length > 0) {
      console.log(`[i18n] ${mod}: removed ${removed.length} stale key(s): ${removed.join(", ")}`);
    }

    const newData: Record<string, Record<string, string>> = {};

    keys.forEach((k) => {
      if (currentOnDisk[k]) {
        // Preserve existing translations, ensuring all langs exist
        newData[k] = { ...currentOnDisk[k] };
        this.config.languages.forEach((l) => {
          if (!newData[k][l]) {
            newData[k][l] = k;
          }
        });
      } else {
        // New key: use key name as default for all langs
        const entry: Record<string, string> = {};
        this.config.languages.forEach((l) => (entry[l] = k));
        newData[k] = entry;
      }
    });

    // Serialize with a trailing newline to match prettier formatting, then
    // compare with current file content to decide whether to write
    const encoded = JSON.stringify(newData, null, 2).trimEnd() + "\n";
    if (encoded !== (currentContent || "")) {
      await fs.outputFile(jsonPath, encoded);
    }

    await this.generateModuleArtifacts(mod, newData);
  }

  // === Artifact Generation (Optimized IF statements) ===
  private async generateModuleArtifacts(mod: string, data?: Record<string, Record<string, string>>) {
    if (!data) {
      try {
        data = JSON.parse(await fs.readFile(resolvePath(this.config, this.config.i18nPath, `${mod}.json`), "utf-8"));
      } catch {
        return;
      }
    }

    const langVars = this.config.languages.map(toVar);
    // Svelte mode reads the runes state directly; node mode calls getLang()
    const langRef = this.config.mode === "node" ? "getLang()" : "i18nState.l";
    const lines = [
      `// Auto-generated module: ${mod}`,
      `import { ${this.config.mode === "node" ? "getLang" : "i18nState"} } from "${this.config.stateModule}";`,
      `import { ${langVars.join(", ")} } from "./constants";`,
      "",
    ];

    // iter key
    Object.keys(data!).forEach((key) => {
      const allArgs = new Set<string>();
      this.config.languages.forEach((lang) => {
        const text = data![key][lang] || "";
        const matches = text.match(ARG_REGEX);
        if (matches) matches.forEach((m) => allArgs.add(m.slice(1, -1)));
      });
      const args = Array.from(allArgs);

      let signature = "()";
      if (args.length > 0) {
        const typeDef = `{ ${args.map((a) => `${a}: any`).join("; ")} }`;
        signature = `(p: ${typeDef})`;
      }
      lines.push(`// t.${mod}.${key}()`);
      lines.push(`export const ${key} = ${signature} => {`);
      const fallbackLang = this.config.languages[0];
      const fallbackText = data![key][fallbackLang] || key;
      const fallbackImpl = fallbackText.replace(/\{(\w+)\}/g, (_, v) => `\${p.${v}}`);

      // iter lang
      for (let i = 1; i < this.config.languages.length; i++) {
        const lang = this.config.languages[i];
        const varName = toVar(lang);
        const text = data![key][lang] || key;
        const impl = text.replace(/\{(\w+)\}/g, (_, v) => `\${p.${v}}`);
        lines.push(`  if (${langRef} === ${varName}) return \`${impl}\`;`);
      }
      lines.push(`  return \`${fallbackImpl}\`;`);
      lines.push(`};`);
    });
    lines.push("");

    await fs.outputFile(resolvePath(this.config, this.config.genPath, `${mod}.ts`), lines.join("\n"));
  }

  private async updateEntryFiles(activeSet: Set<string>) {
    const modules = Array.from(activeSet).sort();
    const langVars = this.config.languages.map(toVar);

    // constants.ts
    const constLines = [
      `// Language Constants`,
      ...this.config.languages.map((l) => `export const ${toVar(l)} = "${l}";`),
      ``,
      `export const SUPPORTED_LANGS = [${langVars.join(", ")}] as const;`,
      `export const DEFAULT_LANG = ${toVar(this.config.languages[0])};`,
      "",
    ];
    await fs.outputFile(resolvePath(this.config, this.config.genPath, "constants.ts"), constLines.join("\n"));

    // index.ts
    const lines = [
      `// Aggregation Entry`,
      ...modules.map((m) => `import * as ${m} from "./${m}";`),
      ``,
      `export const t = {`,
      ...modules.map((m) => `  ${m},`),
      `};`,
      `export * from "./constants";`,
      ...(this.config.mode === "node" ? [`export { setLang, getLang } from "./state";`] : []),
      "",
    ];
    await fs.outputFile(resolvePath(this.config, this.config.genPath, "index.ts"), lines.join("\n"));

    // Node mode: (re)generate the language state module
    if (this.config.mode === "node") {
      await fs.outputFile(resolvePath(this.config, this.config.genPath, "state.ts"), this.stateModuleContent());
    }
  }

  private async removeModule(mod: string) {
    await Promise.all([
      fs.remove(resolvePath(this.config, this.config.i18nPath, `${mod}.json`)),
      fs.remove(resolvePath(this.config, this.config.genPath, `${mod}.ts`)),
    ]);
  }

  // === Helpers ===

  private getAllActiveModules(): Set<string> {
    const s = new Set<string>();
    this.fileCache.forEach((m) => Object.keys(m).forEach((k) => s.add(k)));
    return s;
  }

  private async getDiskModules(): Promise<string[]> {
    const files = await glob(`${this.config.i18nPath}/*.json`, { cwd: this.config.cwd });
    return files.map((f) => path.basename(f, ".json"));
  }

  private async reconcileAll() {
    const active = this.getAllActiveModules();
    await this.updateModules(Array.from(active));
    await this.runPipeline();
  }

  private areSetsEqual(a: Set<string>, b: Set<string>) {
    if (a.size !== b.size) return false;
    for (const i of a) if (!b.has(i)) return false;
    return true;
  }
}

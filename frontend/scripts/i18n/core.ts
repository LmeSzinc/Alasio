import fs from "fs-extra";
import path from "path";
import glob from "fast-glob";
import { type I18nConfig, resolvePath } from "./config";

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
    console.log("[i18n] Initializing (Static Mode)...");

    // Ensure directories exist
    await fs.ensureDir(resolvePath(this.config.i18nPath));
    await fs.ensureDir(resolvePath(this.config.genPath));

    // === SAFEGUARD: Create empty entry files if they don't exist ===
    // This prevents Vite from crashing with "Module not found" on first run.
    if (!fs.existsSync(resolvePath(this.config.genPath, "index.ts"))) {
      await this.createEmptyEntry();
    }

    const files = await glob(`${this.config.srcPath}/**/*.{svelte,ts,js}`, {
      cwd: this.config.cwd,
      absolute: true,
      ignore: [
        // Ignore generated files
        this.config.genPath,
        // Ignore i18n framework internals
        "src/lib/i18n/**",
        // Optional: Ignore tests
        "**/*.test.ts",
        "**/*.test.js",
        "**/*.spec.ts",
        "**/*.spec.js",
      ],
    });

    await Promise.all(files.map((file) => this.scanFile(file, false)));
    await this.reconcileAll();

    console.log(`[i18n] Initialization complete in ${Date.now() - start}ms`);
  }

  /**
   * Creates dummy index.ts and constants.ts so the app can boot up.
   */
  private async createEmptyEntry() {
    const constPath = resolvePath(this.config.genPath, "constants.ts");
    const indexPath = resolvePath(this.config.genPath, "index.ts");

    // Generate constants
    const langVars = this.config.languages.map((l) => `export const ${toVar(l)} = '${l}';`);
    const constContent = [
      ...langVars,
      `export const SUPPORTED_LANGS = [${this.config.languages.map((l) => `'${l}'`).join(", ")}] as const;`,
      `export const DEFAULT_LANG = '${this.config.languages[0]}';`,
      "",
    ].join("\n");
    await fs.outputFile(constPath, constContent);

    // Generate empty t object
    const indexContent = [
      `export * from './constants';`,
      `export const t = {};`, // Proxy in runtime will handle this empty object
      "",
    ].join("\n");
    await fs.outputFile(indexPath, indexContent);
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
    } catch (e) {
      if (this.fileCache.has(filePath)) {
        const oldKeys = Object.keys(this.fileCache.get(filePath)!);
        this.fileCache.delete(filePath);
        return new Set(oldKeys);
      }
      return null;
    }
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
   * Logic: Read JSON -> Merge Keys -> Write JSON -> Generate TS
   */
  private async syncJsonAndGen(mod: string, keys: Set<string>) {
    const jsonPath = resolvePath(this.config.i18nPath, `${mod}.json`);
    let current: Record<string, Record<string, string>> = {};

    try {
      current = JSON.parse(await fs.readFile(jsonPath, "utf-8"));
    } catch {}

    const newData: Record<string, Record<string, string>> = {};
    let changed = false;

    keys.forEach((k) => {
      if (!current[k]) {
        // New key
        newData[k] = {};
        this.config.languages.forEach((l) => (newData[k][l] = k));
        changed = true;
      } else {
        // Existing key
        newData[k] = current[k];
        // Ensure all langs exist
        this.config.languages.forEach((l) => {
          if (!newData[k][l]) {
            newData[k][l] = k;
            changed = true;
          }
        });
      }
    });

    // Check if keys were removed
    if (Object.keys(current).length !== keys.size) changed = true;

    if (changed) {
      await fs.outputFile(jsonPath, JSON.stringify(newData, null, 2));
    }
    await this.generateModuleArtifacts(mod, newData);
  }

  // === Artifact Generation (Optimized IF statements) ===
  private async generateModuleArtifacts(mod: string, data?: Record<string, Record<string, string>>) {
    if (!data) {
      try {
        data = JSON.parse(await fs.readFile(resolvePath(this.config.i18nPath, `${mod}.json`), "utf-8"));
      } catch {
        return;
      }
    }

    const langVars = this.config.languages.map(toVar);
    const lines = [
      `// Auto-generated module: ${mod}`,
      `import { i18nState } from '$lib/i18n/state.svelte';`,
      `import { ${langVars.join(", ")} } from './constants';`,
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
        lines.push(`  if (i18nState.l === ${varName}) return \`${impl}\`;`);
      }
      lines.push(`  return \`${fallbackImpl}\`;`);
      lines.push(`};`);
    });
    lines.push("");

    await fs.outputFile(resolvePath(this.config.genPath, `${mod}.ts`), lines.join("\n"));
  }

  private async updateEntryFiles(activeSet: Set<string>) {
    const modules = Array.from(activeSet).sort();
    const langVars = this.config.languages.map(toVar);

    // constants.ts
    const constLines = [
      `// Language Constants`,
      ...this.config.languages.map((l) => `export const ${toVar(l)} = '${l}';`),
      ``,
      `export const SUPPORTED_LANGS = [${langVars.join(", ")}] as const;`,
      `export const DEFAULT_LANG = ${toVar(this.config.languages[0])};`,
      "",
    ];
    await fs.outputFile(resolvePath(this.config.genPath, "constants.ts"), constLines.join("\n"));

    // index.ts
    const lines = [
      `// Aggregation Entry`,
      ...modules.map((m) => `import * as ${m} from './${m}';`),
      ``,
      `export const t = {`,
      ...modules.map((m) => `  ${m},`),
      `};`,
      `export * from './constants';`,
      "",
    ];

    await fs.outputFile(resolvePath(this.config.genPath, "index.ts"), lines.join("\n"));
  }

  private async removeModule(mod: string) {
    await Promise.all([
      fs.remove(resolvePath(this.config.i18nPath, `${mod}.json`)),
      fs.remove(resolvePath(this.config.genPath, `${mod}.ts`)),
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

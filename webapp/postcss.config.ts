/** @type {import('postcss-load-config').Config} */
import postcssColorMixFunction from "@csstools/postcss-color-mix-function";
import postcssOklabFunction from "@csstools/postcss-oklab-function";
import * as culori from "culori";
import postcss, { type AtRule, type Declaration, type Root, type Rule } from "postcss";
import valueParser from "postcss-value-parser";
// @ts-ignore, no @types/postcss-media-minmax so we just ignore
import postcssMediaMinmax from "postcss-media-minmax";
import postcssNesting from "postcss-nesting";

/*
  Required Dependencies:
  pnpm add -D culori postcss postcss-nesting postcss-value-parser postcss-media-minmax
  pnpm add -D @csstools/postcss-oklab-function @csstools/postcss-color-mix-function

  Types:
  pnpm add -D @types/culori
*/

/*
    This plugin polyfills @property definitions with regular CSS variables
    Additionally, it removes `in <colorspace>` after `to left` or `to right` gradient args for older browsers
*/
const propertyInjectPlugin = () => {
  return {
    Once(root: Root) {
      const fallbackRules: string[] = [];

      // 1. Collect initial-value props from @property at-rules
      root.walkAtRules("property", (rule: AtRule) => {
        const declarations: Record<string, string> = {};
        let varName: string | null = null;

        rule.walkDecls((decl: Declaration) => {
          if (decl.prop === "initial-value") {
            varName = rule.params.trim();
            declarations[varName] = decl.value;
          }
        });

        if (varName) {
          fallbackRules.push(`${varName}: ${declarations[varName]};`);
        }
      });

      // 2. Inject fallback variables if any exist
      if (fallbackRules.length > 0) {
        const fallbackCSS = `@supports not (background: paint(something)) {
                    :root { ${fallbackRules.join(" ")} }
                }`;

        const sourceFile = root.source?.input?.file || root.source?.input?.from;
        const fallbackAst = postcss.parse(fallbackCSS, { from: sourceFile });

        let lastImportIndex = -1;
        root.nodes.forEach((node, i) => {
          if (node.type === "atrule" && node.name === "import") {
            lastImportIndex = i;
          }
        });

        if (lastImportIndex === -1) {
          root.prepend(fallbackAst);
        } else {
          root.insertAfter(root.nodes[lastImportIndex], fallbackAst);
        }
      }

      // 3. Remove `in <colorspace>` for gradients
      root.walkDecls((decl: Declaration) => {
        if (!decl.value) return;
        decl.value = decl.value.replaceAll(/\bto\s+(left|right)\s+in\s+[\w-]+/g, (_, direction) => {
          return `to ${direction}`;
        });
      });
    },
    postcssPlugin: "postcss-property-polyfill",
  };
};

propertyInjectPlugin.postcss = true;

/* 
   Core state sharing: Store all variable names with alpha channel 
   Example: Set { '--border', '--backdrop' }
*/
const alphaVariables = new Set();
/*
    This plugin converts oklch() colors in CSS custom properties to RGB triplets using Culori
    Input:  --border: oklch(1 0 0 / 10%);
    Output: 
      --border: #ffffff1a;         (Fallback)
      --border-rgb: 255 255 255;   (Channels)
      --border-a: 0.1;             (Base Alpha)
*/
const oklchToRgbDoubleDefPlugin = () => {
  return {
    Once(root: Root) {
      root.walkDecls((decl: Declaration) => {
        // Only process CSS custom properties
        if (!decl.prop.startsWith("--")) return;
        // Check if value contains oklch
        if (!decl.value.includes("oklch")) return;

        try {
          // 1. Parse the color using culori
          // This handles complicated syntax like / alpha automatically
          const parsed = culori.parse(decl.value);

          // Ensure it is a valid color and specifically oklch (optional check)
          if (!parsed) return;

          // 2. Convert to RGB and map to sRGB Gamut
          // 'toGamut' ensures that colors outside sRGB (which oklch allows)
          // are clamped correctly to the nearest visible RGB color.
          const toRgb = culori.toGamut("rgb", "oklch");
          const rgbColor = toRgb(parsed);

          // 3. Extract channels (0-255 range)
          const r = Math.round(rgbColor.r * 255);
          const g = Math.round(rgbColor.g * 255);
          const b = Math.round(rgbColor.b * 255);
          const alpha = rgbColor.alpha !== undefined ? parseFloat(rgbColor.alpha.toFixed(4)) : 1;

          // 4. Construct the output string
          let channelString = `${r} ${g} ${b}`;

          // 5. Insert the NEW *-rgb variable AFTER the current one
          // This allows Tailwind to find --var-rgb
          decl.parent!.insertAfter(decl, {
            prop: `${decl.prop}-rgb`,
            value: channelString,
          });

          // 6. Update the ORIGINAL variable to be a standard Hex string
          // This ensures broad compatibility (Chrome 108, older Safari, etc.)
          // e.g. --primary: #3b82f6;
          if (alpha < 1) {
            alphaVariables.add(decl.prop);
            decl.value = culori.formatHex8(rgbColor);
            decl.parent!.insertAfter(decl, {
              prop: `${decl.prop}-a`,
              value: alpha.toString(),
            });
          } else {
            decl.value = culori.formatHex(rgbColor);
          }
        } catch (error) {
          // If parsing fails (e.g. var inside oklch), leave it alone
          console.warn(`Could not convert color in ${decl.prop}: ${decl.value}`);
        }
      });
    },

    postcssPlugin: "postcss-oklch-to-rgb-triplet",
  };
};

oklchToRgbDoubleDefPlugin.postcss = true;

/*
    This plugin converts color-mix() to RGB triplet with alpha format
    Example: color-mix(in oklab, var(--foreground) 20%, transparent) -> rgb(var(--foreground) / 0.2)
*/
const colorMixToAlphaPlugin = () => {
  return {
    Once(root: Root) {
      root.walkDecls((decl: Declaration) => {
        const originalValue = decl.value;
        if (!originalValue || !originalValue.includes("color-mix(")) return;

        const parsed = valueParser(originalValue);
        let modified = false;

        parsed.walk((node) => {
          if (node.type === "function" && node.value === "color-mix") {
            const nodeString = valueParser.stringify(node);
            const match = nodeString.match(
              /color-mix\(in\s+\w+,\s*var\((--[\w-]+)\)\s*(\d+(?:\.\d+)?)%,\s*transparent\)/,
            );

            if (match) {
              const varName = match[1];
              const percentage = parseFloat(match[2]);
              const opacityMod = (percentage / 100).toFixed(4).replace(/\.?0+$/, ""); // 0.5

              let newValue;

              // Core logic: Does this variable have original transparency?
              if (alphaVariables.has(varName)) {
                // Yes! Generate multiplication logic
                // Result: rgb(var(--border-rgb) / calc(var(--border-a) * 0.5))
                newValue = `rgb(var(${varName}-rgb) / calc(var(${varName}-a, 1) * ${opacityMod}))`;
              } else {
                // No! Generate normal logic
                // Result: rgb(var(--border-rgb) / 0.5)
                newValue = `rgb(var(${varName}-rgb) / ${opacityMod})`;
              }

              (node as any).type = "word";
              node.value = newValue;
              delete (node as any).nodes;
              modified = true;
            }
          }
        });

        if (modified) {
          decl.value = parsed.toString();
        }
      });
    },

    postcssPlugin: "postcss-color-mix-to-alpha",
  };
};

colorMixToAlphaPlugin.postcss = true;

/*
    This plugin transforms shorthand rotate/scale/translate into their transform[3d] counterparts
*/
const transformShortcutPlugin = () => {
  return {
    Once(root: Root) {
      const defaults: Record<string, any[]> = {
        rotate: [0, 0, 1, "0deg"],
        scale: [1, 1, 1],
        translate: [0, 0, 0],
      };

      const fallbackAtRule = postcss.atRule({
        name: "supports",
        params: "not (translate: 0)",
        source: root.source,
      });

      root.walkRules((rule: Rule) => {
        let hasTransformShorthand = false;
        const transformFunctions: string[] = [];

        rule.walkDecls((decl: Declaration) => {
          if (/^(rotate|scale|translate)$/.test(decl.prop)) {
            hasTransformShorthand = true;
            const newValues = [...defaults[decl.prop]];
            const value = decl.value.replaceAll(/\)\s*var\(/g, ") var(");
            const userValues = postcss.list.space(value);

            if (decl.prop === "rotate" && userValues.length === 1) {
              newValues.splice(-1, 1, ...userValues);
            } else {
              newValues.splice(0, userValues.length, ...userValues);
            }
            transformFunctions.push(`${decl.prop}3d(${newValues.join(",")})`);
          }
        });

        if (hasTransformShorthand && transformFunctions.length > 0) {
          const fallbackRule = postcss.rule({
            selector: rule.selector,
            source: rule.source,
          });
          fallbackRule.append({
            prop: "transform",
            value: transformFunctions.join(" "),
          });
          fallbackAtRule.append(fallbackRule);
        }
      });

      if (fallbackAtRule.nodes && fallbackAtRule.nodes.length > 0) {
        root.append(fallbackAtRule);
      }
    },

    postcssPlugin: "postcss-transform-shortcut",
  };
};

transformShortcutPlugin.postcss = true;

/**
 * PostCSS plugin to transform empty fallback values from `var(--foo,)`,
 * turning them into `var(--foo, )`. Older browsers need this.
 */
const addSpaceForEmptyVarFallback = () => {
  return {
    OnceExit(root: Root) {
      root.walkDecls((decl: Declaration) => {
        if (!decl.value || !decl.value.includes("var(")) return;

        const parsed = valueParser(decl.value);
        let changed = false;

        parsed.walk((node) => {
          if (node.type === "function" && node.value === "var") {
            const commaIndex = node.nodes.findIndex((n) => n.type === "div" && n.value === ",");
            if (commaIndex === -1) return;

            const fallbackNodes = node.nodes.slice(commaIndex + 1);
            const fallbackText = fallbackNodes
              .map((n) => n.value)
              .join("")
              .trim();

            if (fallbackText === "") {
              const commaNode = node.nodes[commaIndex];
              if (commaNode.value === ",") {
                commaNode.value = ", ";
                changed = true;
              }
            }
          }
        });

        if (changed) {
          decl.value = parsed.toString();
        }
      });
    },
    postcssPlugin: "postcss-add-space-for-empty-var-fallback",
  };
};

addSpaceForEmptyVarFallback.postcss = true;

/*
    Promote Plugin (Take-over Plugin)
    Purpose:
    1. Find "rgb(...)" definitions wrapped in @supports.
    2. Find the corresponding "solid color fallback" definition outside.
    3. Override the old definition outside with the new definition inside, and remove @supports.
    
    Result:
    Input:
      .bg-primary/20 { background: var(--primary); }
      @supports (color: color-mix(...)) {
        .bg-primary/20 { background: rgb(var(--primary-rgb) / 0.2); }
      }
      
    Output:
      .bg-primary/20 { background: rgb(var(--primary-rgb) / 0.2); }
*/
const promoteColorMixPlugin = () => {
  return {
    postcssPlugin: "postcss-promote-color-mix",
    OnceExit(root: Root) {
      // 1. Iterate through all @supports
      root.walkAtRules("supports", (atRule: AtRule) => {
        // Only process checks for color-mix
        if (!atRule.params.includes("color-mix")) return;

        // 2. Iterate through rules inside @supports
        atRule.walkRules((innerRule: Rule) => {
          // 3. Find the "outer rule" in the root node with the same selector as the inner rule
          // We look upward (prev), usually the adjacent one is the outer rule
          let outerRule = atRule.prev();

          // If the previous one is not a rule (might be a comment or other), continue searching upward
          while (outerRule && outerRule.type !== "rule") {
            outerRule = outerRule.prev();
          }

          // Matched the outer rule, and the selector is consistent
          if (outerRule && outerRule.type === "rule" && outerRule.selector === innerRule.selector) {
            // 4. Move the inner declarations to the outer rule
            innerRule.walkDecls((innerDecl: Declaration) => {
              // Find the property with the same name in the outer rule (e.g. border-color)
              let replaced = false;
              (outerRule as Rule).walkDecls(innerDecl.prop, (outerDecl: Declaration) => {
                // Override it!
                outerDecl.value = innerDecl.value;
                // If there are !important or other flags, they can also be synchronized here
                outerDecl.important = innerDecl.important;
                replaced = true;
              });

              // If the outer rule doesn't have this property (rare), append it directly
              if (!replaced) {
                outerRule.append(innerDecl.clone());
              }
            });

            // Mark the inner rule as processed (optional, will be cleaned up when @supports is deleted)
          } else {
            // If the outer rule is not found (e.g. this is a brand new definition), extract the rule directly
            // Insert it before @supports
            atRule.parent!.insertBefore(atRule, innerRule);
          }
        });

        // 5. After extracting the value, delete this @supports block
        atRule.remove();
      });
    },
  };
};
promoteColorMixPlugin.postcss = true;

const config = {
  plugins: [
    propertyInjectPlugin(),
    oklchToRgbDoubleDefPlugin(),
    colorMixToAlphaPlugin(),
    transformShortcutPlugin(),
    addSpaceForEmptyVarFallback(),
    postcssMediaMinmax,
    postcssOklabFunction,
    postcssNesting,
    postcssColorMixFunction,
    // Must be the last plugin
    promoteColorMixPlugin(),
  ],
};

export default config;

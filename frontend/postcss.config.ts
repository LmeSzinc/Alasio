/* eslint-disable @typescript-eslint/no-require-imports */
/** @type {import('postcss-load-config').Config} */
const postcss = require("postcss");
const valueParser = require("postcss-value-parser");
const culori = require("culori");

/*
    This plugin polyfills @property definitions with regular CSS variables
    Additionally, it removes `in <colorspace>` after `to left` or `to right` gradient args for older browsers
*/
const propertyInjectPlugin = () => {
  return {
    Once(root) {
      const fallbackRules = [];

      // 1. Collect initial-value props from @property at-rules
      root.walkAtRules("property", (rule) => {
        const declarations = {};
        let varName = null;

        rule.walkDecls((decl) => {
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
      root.walkDecls((decl) => {
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
   核心状态共享：存储所有带有 alpha 通道的变量名 
   例如：Set { '--border', '--backdrop' }
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
    Once(root) {
      root.walkDecls((decl) => {
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
          const toRgb = culori.toGamut("rgb");
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
          decl.parent.insertAfter(decl, {
            prop: `${decl.prop}-rgb`,
            value: channelString,
          });

          // 6. Update the ORIGINAL variable to be a standard Hex string
          // This ensures broad compatibility (Chrome 108, older Safari, etc.)
          // e.g. --primary: #3b82f6;
          if (alpha < 1) {
            alphaVariables.add(decl.prop);
            decl.value = culori.formatHex8(rgbColor);
            decl.parent.insertAfter(decl, {
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
    Once(root) {
      root.walkDecls((decl) => {
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

              // 核心判断：这个变量是否有原始透明度？
              if (alphaVariables.has(varName)) {
                // 有！生成乘法逻辑
                // 结果: rgb(var(--border-rgb) / calc(var(--border-a) * 0.5))
                newValue = `rgb(var(${varName}-rgb) / calc(var(${varName}-a, 1) * ${opacityMod}))`;
              } else {
                // 没有！生成普通逻辑
                // 结果: rgb(var(--border-rgb) / 0.5)
                newValue = `rgb(var(${varName}-rgb) / ${opacityMod})`;
              }

              node.type = "word";
              node.value = newValue;
              delete node.nodes;

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
    Once(root) {
      const defaults = {
        rotate: [0, 0, 1, "0deg"],
        scale: [1, 1, 1],
        translate: [0, 0, 0],
      };

      const fallbackAtRule = postcss.atRule({
        name: "supports",
        params: "not (translate: 0)",
        source: root.source,
      });

      root.walkRules((rule) => {
        let hasTransformShorthand = false;
        const transformFunctions = [];

        rule.walkDecls((decl) => {
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
    OnceExit(root) {
      root.walkDecls((decl) => {
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
    Promote Plugin (鸠占鹊巢插件)
    作用：
    1. 找到被 @supports 包裹的 "rgb(...)" 定义。
    2. 找到它外面对应的 "纯色回退" 定义。
    3. 用里面的新定义覆盖外面的旧定义，并移除 @supports。
    
    结果：
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
    OnceExit(root) {
      // 1. 遍历所有 @supports
      root.walkAtRules("supports", (atRule) => {
        // 只处理针对 color-mix 的检查
        if (!atRule.params.includes("color-mix")) return;

        // 2. 遍历 @supports 内部的规则
        atRule.walkRules((innerRule) => {
          // 3. 在根节点寻找与内部规则选择器相同的 "外部规则"
          // 我们往上看 (prev)，通常紧挨着的就是外部规则
          let outerRule = atRule.prev();

          // 如果前一个不是规则（可能是注释或其他），继续往前找一点
          while (outerRule && outerRule.type !== "rule" && outerRule !== root) {
            outerRule = outerRule.prev();
          }

          // 匹配到了外部规则，且选择器一致
          if (outerRule && outerRule.type === "rule" && outerRule.selector === innerRule.selector) {
            // 4. 将内部的声明 (declarations) 搬运到外部
            innerRule.walkDecls((innerDecl) => {
              // 在外部规则中查找同名属性 (如 border-color)
              let replaced = false;
              outerRule.walkDecls(innerDecl.prop, (outerDecl) => {
                // 覆盖它！
                outerDecl.value = innerDecl.value;
                // 如果有 !important 等标记，也可以在这里同步
                outerDecl.important = innerDecl.important;
                replaced = true;
              });

              // 如果外部规则里居然没这个属性（少见），则直接追加
              if (!replaced) {
                outerRule.append(innerDecl.clone());
              }
            });

            // 标记内部规则已处理（可选，稍后删除 atRule 会统一清理）
          } else {
            // 如果没找到外部规则（比如这是个全新的定义），直接把规则提出来
            // 插入到 @supports 之前
            atRule.parent.insertBefore(atRule, innerRule);
          }
        });

        // 5. 榨干价值后，删除这个 @supports 块
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
    require("postcss-media-minmax"),
    require("@csstools/postcss-oklab-function"),
    require("postcss-nesting"),
    require("@csstools/postcss-color-mix-function"),
    // Must be the last plugin
    promoteColorMixPlugin(),
  ],
};

module.exports = config;

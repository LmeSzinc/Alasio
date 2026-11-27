# Alasio 浏览器兼容性

我们希望保持对win7的兼容，根据 [electron版本](https://www.electronjs.org/docs/latest/tutorial/electron-timelines)，我们选择最后一个兼容win7的版本 electron==22.3.7，对应chromium 108。

### tailwindcss 4 兼容性修复

但是根据 [tailwincss兼容性](https://tailwindcss.com/docs/compatibility) 刚好差一点。

```
Chrome 111 (released March 2023)
Safari 16.4 (released March 2023)
Firefox 128 (released July 2024)
```

然后我网上随机找到了 [tailwindcss v4 polyfill](https://gist.github.com/alexanderbuhler/2386befd7b6b3be3695667cb5cb5e709)，里面最低兼容到

```
iOS Safari 16.1
iOS Safari 15.5
Chrome 99
Chrome 90 (needs special treatment now, see commented polyfills)
```

但又没必要那么低，所以我打算自己决定每个 polyfill

This gist may be your full solution or just the starting point for making your Tailwind v4 projects backwards compatible.

What it does

- Effectively getting browser support down to Chrome 99 (and basically everything supporting `@layer`)
  - 不需要考虑低于 chrome 99
- Pre-compute `oklab()` functions
  - [oklab()](https://caniuse.com/wf-oklab) 从 chrome 111开始支持，需要保留
- Pre-compute `color-mix()` functions (+ replace CSS vars inside them beforehand)
  - [color-mix()](https://caniuse.com/wf-color-mix) 从 chrome 111开始支持，需要保留
- Remove advanced instructions (colorspace) from gradients
  - [lilear-gradient](https://caniuse.com/wf-gradient-interpolation) 从 chrome 111开始支持，需要保留
- Provide support for nested CSS (used by dark mode or custom variants with `&`)
  - [css-neasting](https://caniuse.com/css-nesting) 从 chrome 120 开始完整支持，108完全不支持，需要保留
- Transform `translate`, `scale`, `rotate` properties to their `transform: ...` notation
  - [individual transform properties](https://caniuse.com/wf-individual-transforms) 从 chrome 104 开始支持，不需要
- Add whitespace to var fallbacks `var(--var,)` > `var(--var, )` to help older browsers understand
  - 没有找到具体的资料，ai说是 Safari 10, 11, 12 的bug导致它认为 var(--var,) 是语法错误，而safari 12 对应 iOS 12 对应 iPhone 5s, 6，所以应该是不需要
- Transform `@media` queries from using `width >= X` to old `min-width: X` notation
  - [media-range-syntax](https://caniuse.com/css-media-range-syntax) 从 chrome 104 开始支持，不需要

### color-mix 兼容性修复

其中 color-mix 的兼容性修复比较复杂，上面的gist使用了[@csstools/postcss-color-mix-function](https://www.npmjs.com/package/@csstools/postcss-color-mix-function) 但是这个库说

> [!NOTE] We can not dynamically resolve `var()` arguments in `color-mix()`, only static values will work.

于是在上面的 gist代码中，提取了 var 的颜色然后计算出实际数值。这其实是不对的，因为会有 `bg-foreground/50` 这样的使用，其中我们引用了 `--foreground` 颜色变量，这个变量不是固定的，是根据亮色暗色主题变化的。

一种解法是使用 `rgb(from var(--base-color) r g b / 0.5)` ，但是很遗憾 `from var` 需要到 chrome 119 才支持。

也可以使用 `rgb(var(--base-color) / 0.5)`，但这就需要颜色变量不能是 `rgb(r, g, b)` 的形式，需要是 `r g b`，这确实解决了半透明颜色的问题，但是带来了更大的问题，颜色变量 `--base-color` 不能直接被使用，需要使用 `rgb(var(--base-color))`。

所以最终的解法是，编写多个 postcss 插件

- 为每个颜色生成双重定义，比如 `--desstructive: oklch(0.704 0.191 22.216)` 会生成 `--destructive: #ff6467;` 和 `--destructive-rgb: 255 100 103;` 。如果原始颜色有定义透明度，再追加 alpha 数值比如对于 `--border: oklch(1 0 0 / 10%);` 生成 `--border: #ffffff1a;`， `--border-a: 0.1;`，`--border-rgb: 255 255 255;`
- 在生成双重定义的同时，收集哪些变量有 alpha 通道
- 将每个带 transparent 的 color-mix 函数，改成 `rgb(var(${varName}-rgb) / ${alpha})`，有alpha通道的生成 `rgb(var(${varName}-rgb) / calc(var(${varName}-a) * ${alpha}))`
- 编写一个放在最后的插件，清理重复定义。因为 tailwindcss会生成两份类定义

```css
.bg-primary/20 { background: var(--primary); }
@supports (color: color-mix(...)) {
    .bg-primary/20 { background: rgb(var(--primary-rgb) / 0.2); }
}
```

但实际上，我们已经处理好了color-mix，`@supports` 检查会导致生成的内容被旧版浏览器忽略。因此我们需要提取 `@supports` 中的内容并删掉 fallback 定义，得到

```css
.bg-primary/20 { background: rgb(var(--primary-rgb) / 0.2); }
```

### 兼容性的验证方式

polyfill 把 `oklab()` `color-mix()` 降级成 `rgb()`、把嵌套规则摊平、把媒体查询区间语法换成 `min-width`，但生成一段 CSS 不等于引擎把它画出来，也不等于数值正确。所以每一项都分三层验证，都不需要肉眼确认：

1. **数值**：`frontend/scripts/postcss.test.ts` 用 CSS Color 4 的矩阵和 `color-mix()` 的预乘 alpha 混色公式在测试里重新算一遍期望值，再与插件产物逐通道比较（允许 1/255 的舍入差）。断言的是颜色而不是字符串，插件换一种写法、颜色值不变，测试依然通过；插件把颜色算错，测试就失败。
   - 超出 sRGB 的颜色（例如 `oklab(0.5 -0.1 0.15)`）没有唯一正确答案：不同 gamut mapping 算法的结果可以相差几个字节。这类颜色只断言“先输出 `rgb()` 兜底、再输出 `color(display-p3 …)`”，具体数值交给引擎。
   - 断言用到的色值必须落在 sRGB 内，否则期望值本身就需要一个 gamut mapping 实现，测的就不再是颜色而是某个库的实现选择。
2. **落盘**：同一个测试扫描 `src/**/*.svelte` 的行内 `style` 属性，禁止出现 `oklab(` `oklch(` `color-mix(`。PostCSS 只编译样式表，写在行内属性里的函数永远到不了插件，Chromium 108 会整条丢弃声明——兼容性页面曾经就是这样一片空白。
3. **渲染**：`webapp/scripts/compat/browser-smoke.cjs` 在 electron 22（chromium 108，客户端实际使用的引擎）里加载 `frontend/build` 的全部 CSS，为每个兼容性特性构造一组固定尺寸的探针，然后截图读像素。

渲染阶段的分组与判定：

- `control`：加载未降级的 `oklab()` `color-mix()` 和嵌套规则，要求引擎确实用不了。否则“引擎画出来了”可能只是因为引擎原生支持这些语法，正面用例就说明不了任何问题。
- `colors`：每个 `swatch-*` 格子必须算出它自己那条规则声明的颜色，截图必须显示同一个颜色。
- `gradients`：背景图必须存活（带色彩空间 hint 的渐变在 108 上是“声明存在、计算值为空”），两端像素必须不同。
- `opacity`：`bg-primary/50` 这类声明必须与同色的 `bg-primary` 通道相同、alpha 等于倍率，截图必须是它按页面白底合成后的颜色。同一个窗口里再切一次 `.dark`：主题变量必须真的换掉颜色，否则“暗色也通过”只是因为两边都是亮色。文字色与边框色没有大片区域可采样，只比计算值。
- `transforms`：`translate-x-4`、`scale-125`、`rotate-12` 以及组合必须与显式 `transform: translate(16px, 8px)` 声明落在同一个盒子（偏差 0.6px 内），并且栅格化成相同的像素。
- `nesting`：用 `&` 写法的子元素必须与同色的平铺规则画出同一个颜色，顺带验证 `var(--undefined, #8b5cf6)` 会回退。回退的期望值取自这条声明自己的回退分支（含 `var()` 的 `background` 简写在引擎里不会提前展开成长属性，两处都读），脚本里不含色值常量。
- `variants`：`dark:bg-gray-800` 在 `.dark` 祖先内必须等于该规则声明的颜色，没有 `.dark` 时必须什么都不画。
- `hover`：用真实鼠标事件移到元素上，移动前必须是基础色，移动后必须是 `hover:` 色；`hover:scale-110` 的盒子必须变成 1.1 倍。
- `breakpoints`：把窗口改成 640 / 900 / 1280 宽，`md:` `lg:` 必须在 768 / 1024 处切换。

约定：

- 期望值不写死调色板。颜色取自规则本身的声明，状态色（`hover:` `.dark` 断点）取自已匹配规则的声明并重新施加到一个无状态元素上，transform 的期望是一条引擎必然支持的显式声明。脚本里没有任何色值常量。
- 不存基准图，不做图像相似度。探针是 64px 的纯色块，比的是采样点的 RGB 整数（容差 2/255）；金标准图片只会引入字体、缩放、抗锯齿的噪声，还会让改颜色变成“重新生成图片”的事。
- 截图前关闭硬件加速并固定 `force-color-profile=srgb`、`force-device-scale-factor=1`，让纯色块栅格化成确定的字节。
- Svelte 会给组件的 `<style>` 规则加 `svelte-*` 作用域类，脚本从命中的选择器里取出这个类补到探针上，所以兼容性页面自己的规则（色块、嵌套、变量回退）和 Tailwind 的工具类走同一条路径。
- 探针只带类名、不带页面结构，所以这条不依赖 dev server、登录态和后端。代价是它验证的是“编译后的 CSS 在引擎里的表现”，页面里是否真的用了这些类由单元测试的行内样式扫描和人工过目保证。
- 这条在 CI 的 `smoke` 任务里运行，前一步已经构建过 frontend。本地运行：

  ```powershell
  cd "E:\ProgramData\Pycharm\Alasio\frontend"; pnpm run build; pnpm run smoke:compat
  ```

三层是串联的，少一层就漏一类问题：

- 只有 1 没有 3：颜色算对了，但页面上是空白（就是当时遇到的 bug）。
- 只有 3 没有 1：像素与样式表一致，但样式表本身就是错的颜色，截图会一路绿。像素比对的是“引擎画出的”与“样式表声明的”，它不判断后者是否符合 CSS 规范；那是第 1 层的职责。
- 只有 1、3 没有 2：颜色处理对了，但页面里有一个色块写在行内 `style` 上，从未经过 PostCSS。

已知边界：

- `translate` `scale` `rotate` 从 chromium 104 起原生支持，108 上走的是原生路径；`transformShortcutPlugin` 生成的 `@supports not (translate: 0)` 回退分支在 108 上不会生效，因此没有被这条冒烟覆盖。它服务的是更老的引擎，而客户端不会跑在那些引擎上。
- `var(--x,)` 补空格的修复只对 Safari 的解析器有意义，Electron 里两种写法都合法，无法用渲染结果区分；这一项由 `postcss.test.ts` 的文本断言覆盖。


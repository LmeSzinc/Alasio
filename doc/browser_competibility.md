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


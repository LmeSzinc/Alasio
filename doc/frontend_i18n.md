# 前端i18n模块开发文档

Alasio 的前端和客户端使用统一的定制开发的 i18n 模块，它主要参照了 [ParaglideJS](https://inlang.com/m/gerre34r/library-inlang-paraglideJs)。

在 ParaglideJS 中有很多我们喜欢的理念。

1. **极致的性能与 Tree-shaking**

   ParaglideJS 会将 i18n 文本编译为函数，而不是一个巨大 json，这使得 vite 可以对翻译文本进行完美 tree-shaking 优化，让页面只加载当前需要的 i18n 而不需要加载无关内容，并且无运行时开销。

2. **完全的类型安全**

   因为生成了 TS 代码，所以当你输入 `m.hello_world()` 时，IDE 会有完整的提示，并且会检查函数的参数和类型，有效避免拼写错误导致的运行时错误。

3. **开发时热更新**

   也因为是生成的代码，你每次修改翻译文本的时候就会触发 TS 代码的生成，进而触发 vite 的热更新，即时地反馈在页面上。

但也有一些我不喜欢的地方

1. **我希望翻译文本是按模块管理而不是按语言**

   大部分的 i18n 框架都是按语言管理的，这种设计模式在这几年间给我增加了大量无谓工作。我每次修改一个key，都要在几个超级巨大文本中修改那个对应的key。

   哦，还有那种把翻译文件在第三方网站导出导入的模式更是极品，我本来可以直接修改 json，现在我要对接那个网站 注册帐号 创建项目 学习使用。

   说到底这些管理模式都是给中大型商业项目使用的，他们有专门的翻译人员，开发就只负责写代码。适合小型业余爱好项目的应该是按模块放 json，json 里在分语言。

2. **我不喜欢插件捆绑**

   ParaglideJS 强制捆绑了他自家的 vscode 插件 sherlock，i18n key 的新增其实是借助插件完成的。我希望是与 ide 无关，插件无关的。

## 增加翻译文本

首先我们需要保持 vite 在运行

```
pnpm run dev
```

然后在代码中这样编写

```html
<script lang="ts">
  import { t } from '$lib/i18n';
</script>

<h1>{t.Home.Title()}</h1>
<p>{t.Common.Submit()}</p>
```

> 注意：
>
> 1. 只能使用 `t.Module.Key` 的形式，不能有更多或者更少的层级。
> 2. 当你首次编写 `t.Home.Title()` 的时候，IDE 可能会提示错误，但很快对应的代码就能生成完毕，错误消除。
> 3. 无法检测动态字符串调用，比如你想访问 `t.Module["key${index}"]()` ，i18n模块是无法检测到的。

然后稍等一下下，在 `src/i18n/{Module}.json` 中就会出现 Key，每个 key 下分语言。

翻译文本默认是 key name 本身，你可以继续开发，暂时不理会翻译。

```json
{
  "Login": {
    "en-US": "Login",
    "zh-CN": "登录",
    "ja-JP": "ログイン",
    "zh-TW": "登入",
    "es-ES": "Iniciar Sesión"
  }
}
```

通常我们会将一个页面中的翻译文本集中在一个 i18n 模块中，这样方便管理。你可以手动编写你熟悉的语言的文本，然后把一整个模块的 i18n json 交由 AI 翻译其他语言。

## 增加带参数的翻译文本

跟随前面的内容增加一个翻译文本，然后在翻译文本中添加参数。

```json
{
  "Hello": {
    "en-US": "Hello {name}, it is {day}",
    "zh-CN": "你好 {name}，今天是 {day}"
  }
}
```

然后你的 `t.Home.Hello()` 就是一个需要输入参数的函数了，IDE 应该会给出报错，因为我们还没有输入参数呢。改成像这样输入就可以了。

```html
<p>{t.Profile.Hello({ name: user.name, day: 'Monday' })}</p>
```

## 在运行时切换语言

语言状态我们放在了全局响应式对象 `i18nState` 里。

```typescript
import { i18nState } from "$lib/i18n/state.svelte";
import { L_zh_CN, L_en_US } from "$lib/i18n/constants";

// 切换到中文
i18nState.l = L_zh_CN;
```

借助 Svelte 5 的响应式机制，你只需要改一下这个值，所有用到了 `t.xxx()` 的地方都会自动更新，不需要刷新页面，文本也不会闪烁。

当然，语言切换已经封装在 `src/lib/i18n/LangSelector.svelte` 组件中了，一般不需要手动设置。

## 它如何工作的？

i18n 模块在 `scripts/i18n` ，其中有 vite 插件监听开发目录的文件变动。

每当你编写 `t.Home.Title()` 这样的内容的时候，i18n 模块就会维护 `src/i18n/Home.json` 中的数据结构，包括给新增的键填充默认文本，删除未使用的旧键。

然后同步维护 `src/i18ngen/Home.ts`，生成像这样的 TS 函数

```typescript
export const Title = (p: { name: any }) => {
  if (i18nState.l === L_zh_CN) return `欢迎, ${p.name}`;
  if (i18nState.l === L_ja_JP) return `ようこそ, ${p.name}`;
  return `Welcome, ${p.name}`;
};
```

然后同步维护 `src/i18ngen/index.ts` 中的模块导出

```typescript
// Aggregation Entry
import * as AssetManager from './AssetManager';
import * as Auth from './Auth';
...

export const t = {
  AssetManager,
  Auth,
  ...
};
export * from './constants';
```

最后你的 IDE 就会认识 `t.Home.Title()` 这个函数了

## 目录结构

- `scripts/i18n/`：i18n 模块目录。
  - `core.ts`：核心逻辑，负责扫描、解析和生成。
  - `config.ts`：配置文件。
  - `vite.ts`：Vite 插件的集成代码。
- `src/i18n/`：**[数据源]** 这里存放 JSON 翻译文件。请务必把它们提交到 Git。
- `src/i18ngen/`：**[生成产物]** 这里是自动生成的 TS 代码。通常不需要提交到 Git，只是构建时生成。

## 增加新的语言

如果你需要支持新的语言，可以在 `scripts/i18n/config.ts` 里配置。

```typescript
export const i18nConfig: I18nConfig = {
  // ...
  // 支持的语言列表，第一项是默认/Fallback语言
  languages: ["en-US", "zh-CN", "ja-JP", "zh-TW", "es-ES"],
};
```

修改之后需要完全重启开发服务器。

## 来自后端的翻译文本

还有一些文本的翻译是来自后端的，比如设置页面中的设置名称和帮助文本、任务队列的任务名称，都是 mod specific，后端返回的时候会将 name help 本身替换为对应语言的文本。

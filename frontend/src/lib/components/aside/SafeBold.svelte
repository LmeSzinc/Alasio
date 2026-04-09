<script lang="ts">
  import { cn } from "$lib/utils";
  import { elementSize } from "$src/lib/use/size.svelte";
  import { untrack } from "svelte";

  interface Props {
    text: string;
    active?: boolean;
    class?: string;
    boldClass?: string;
    containerClass?: string;
  }

  let {
    text = "",
    active = false,
    class: baseClass = "",
    boldClass = "font-bold",
    containerClass = "",
  }: Props = $props();

  let probeRef = $state<HTMLElement | null>(null);
  let probeSize = $state({ width: 0, height: 0 });
  let lines = $state<string[][]>([]); // [[word, word], [word]]

  // 核心计算函数：在高度确定后执行
  const calculateLines = () => {
    if (!probeRef || !text) return;
    const spans = probeRef.querySelectorAll("span");
    if (spans.length === 0) return;

    const words = text.split(/\s+/);
    const newLines: string[][] = [];
    let currentLine: string[] = [];
    let lastTop = -1;

    spans.forEach((span, index) => {
      // 获取当前 span 距离探测器顶部的距离
      const top = (span as HTMLElement).offsetTop;

      // 如果 top 变大，说明这个单词折到了下一行
      if (lastTop !== -1 && top > lastTop) {
        newLines.push(currentLine);
        currentLine = [words[index]];
      } else {
        currentLine.push(words[index]);
      }
      lastTop = top;
    });

    if (currentLine.length > 0) {
      newLines.push(currentLine);
    }

    lines = newLines;
  };

  // 当探测器高度由于任何原因发生变化时（包括宽度改变、字体加载、初始渲染），重新计算
  // 当文字内容本身改变时，也需要重新计算
  $effect(() => {
    probeRef;
    text;
    probeSize.height;
    if (!probeRef || !text) return;
    if (probeSize.height <= 0) return;
    untrack(calculateLines);
  });
</script>

<!-- 
  Wrapper component to create a text that is font-base if inactive and font-bold if active, 
  and doesn't affect the layout
  It avoids the layout shift when the text is toggled between bold and normal.
-->
<div class={cn("relative w-full whitespace-normal", containerClass)}>
  <!-- 
    探测层 (Probe)：
    - 始终加粗，高度由浏览器布局决定。
    - 用 span 包裹每个单词以便测量 offsetTop。
    - 这里的 absolute 配合 w-full 确保它遵循侧边栏给出的真实可用宽度。
  -->
  <div
    bind:this={probeRef}
    use:elementSize={probeSize}
    class={cn("pointer-events-none invisible absolute top-0 left-0 w-full", baseClass, boldClass)}
    aria-hidden="true"
  >
    {#each text.split(/\s+/) as word}
      <span class="inline-block">{word}</span>{" "}
    {/each}
  </div>

  <!-- 
    显示层：
    - 基于计算出来的 lines 渲染。
    - 每一个 line 都是一个块级 div，确保强制换行。
    - 即使是不加粗状态，也会被这里的 div 强行切断。
  -->
  <div class={cn(baseClass, active ? boldClass : "font-normal")}>
    {#if lines.length > 0}
      {#each lines as line}
        <div class="flex flex-wrap">
          {line.join(" ")}
        </div>
      {/each}
    {:else}
      <!-- 初始白屏时的兜底，不带分行逻辑 -->
      <div class="opacity-0">{text}</div>
    {/if}
  </div>
</div>

<script lang="ts">
  import ThemeToggle from "$lib/components/ui/theme/theme-toggle.svelte";
  import LangSelector from "$lib/i18n/LangSelector.svelte";
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  // props
  type $$props = {
    children?: Snippet;
    class?: string;
  };
  let { children, class: className }: HTMLAttributes<HTMLHeadElement> & $$props = $props();
</script>

<header
  class={cn(
    // global
    "app-header header-shadow relative z-40",
    "bg-card flex h-12.5 w-full items-center gap-1 px-4",
    className,
  )}
>
  <!-- header content -->
  {@render children?.()}

  <LangSelector></LangSelector>
  <ThemeToggle variant="ghost"></ThemeToggle>
</header>

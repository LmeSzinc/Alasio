<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";
  import { Menu } from "@lucide/svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import AppHeader from "./AppHeader.svelte";
  import { HeaderContext } from "$lib/slotcontext.svelte";

  // props
  type $$props = {
    class?: string;
    onMenuClick: () => void;
  };
  let { class: className, onMenuClick }: HTMLAttributes<HTMLHeadElement> & $$props = $props();
</script>

<AppHeader class={cn("", className)}>
  <!-- Mobile menu button, hidden on medium screens and up -->
  <Button variant="ghost" size="icon" class="shrink-0 md:hidden" onclick={onMenuClick}>
    <Menu class="h-5 w-5" />
    <span class="sr-only">Toggle navigation menu</span>
  </Button>

  <!-- Other header content -->
  {#if HeaderContext.snippet}
    {@render HeaderContext.snippet()}
  {:else}
    <h1 class="w-full flex-1 text-center text-lg">Header</h1>
  {/if}
</AppHeader>

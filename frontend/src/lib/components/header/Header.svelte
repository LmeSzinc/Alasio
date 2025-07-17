<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";
  import { Menu } from "@lucide/svelte";
  import type { HTMLAttributes } from "svelte/elements";

  // props
  type $$props = {
    onMenuClick: () => void;
  };
  let { class: className, onMenuClick }: HTMLAttributes<HTMLHeadElement> & $$props = $props();
</script>

<header class={cn("bg-background flex h-12.5 w-full items-center gap-4 px-4 pr-13", className)}>
  <!-- Mobile menu button, hidden on medium screens and up -->
  <Button variant="ghost" size="icon" class="shrink-0 md:hidden" onclick={onMenuClick}>
    <Menu class="h-5 w-5" />
    <span class="sr-only">Toggle navigation menu</span>
  </Button>

  <!-- Other header content -->
  <div class="w-full flex-1 text-center">Header</div>
</header>

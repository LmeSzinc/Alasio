<script lang="ts">
  import { cn } from "$lib/utils";

  let { color, class: className }: { color: string; class?: string } = $props();

  // #RGB, #RGBA, #RRGGBB, #RRGGBBAA
  const hexRegex = /^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{4}|[A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$/;
  const safeColor = $derived(hexRegex.test(color) ? color : "#777777");
</script>

<span class={cn("inline-block h-6 w-6 rounded-full", className)} style:background-color={safeColor}></span>

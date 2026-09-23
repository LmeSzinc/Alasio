<script lang="ts">
  import { cn } from "$lib/utils";

  let { color, class: className }: { color: string; class?: string } = $props();

  // #RGB, #RGBA, #RRGGBB, #RRGGBBAA
  const hexRegex = /^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{4}|[A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$/;
  const safeColor = $derived(hexRegex.test(color) ? color : "#777777");
</script>

<!--
  The color of the config is handed to the dot through a custom property, and a
  class of its own paints with it (`bg-(--dot-color)`). That way a caller can
  override the dot with a plain color class (`bg-primary-foreground` while the
  dashboard item flashes, see DashboardItem); an inline `background-color`
  would take the config color out of the class system, and nothing in a class
  could ever win against it.
-->
<div class={cn("h-6 w-6 shrink-0 rounded-full bg-(--dot-color)", className)} style:--dot-color={safeColor}></div>

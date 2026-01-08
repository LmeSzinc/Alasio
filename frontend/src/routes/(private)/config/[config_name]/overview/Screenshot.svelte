<script lang="ts">
  import { cn } from "$lib/utils";
  type $$Props = {
    class?: string;
  };
  let { class: className }: $$Props = $props();
</script>

<div class={cn("neushadow bg-card rounded-lg p-4", "aspect-video", className)}>
  <h2 class="mb-6 text-2xl font-bold">Screenshot</h2>
</div>

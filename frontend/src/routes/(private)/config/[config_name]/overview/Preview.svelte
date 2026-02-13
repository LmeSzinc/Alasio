<script lang="ts">
  import { cn } from "$lib/utils";
  type $$Props = {
    class?: string;
    config_name: string;
  };
  let { class: className, config_name }: $$Props = $props();
</script>

<div class={cn("neushadow bg-card rounded-lg p-4", "aspect-video", className)}>
  <h2 class="mb-6 text-2xl font-bold">Screenshot</h2>
</div>

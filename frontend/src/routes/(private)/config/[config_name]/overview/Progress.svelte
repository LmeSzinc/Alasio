<script lang="ts">
  import { cn } from "$lib/utils";

  type $Props = {
    class?: string;
  };
  let { class: className }: $Props = $props();
</script>

<!-- Placeholder of the progress bar, which the backend does not report yet.
     The box keeps its size on the overview page, so adding the bar later does
     not reflow the preview above it. -->
<div class={cn("neushadow bg-card rounded-lg p-4", className)}></div>

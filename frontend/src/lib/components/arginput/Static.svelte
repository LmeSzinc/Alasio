<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { cn } from "$lib/utils";

  let { data = $bindable(), class: className }: InputProps = $props();
  // static component is read-only, but we still use useArgValue to handle reactive updates from parent
  const arg = $derived(useArgValue<any>(data));
</script>

<div class={cn("bg-card dark:bg-card relative flex h-7 w-full p-1 px-2", className)}>
  <span class="text-primary/80 truncate font-semibold">
    {arg.getLabel(arg.value)}
  </span>
</div>

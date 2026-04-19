<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { cn } from "$lib/utils";

  let {
    data = $bindable(),
    class: className,
    isDesc,
    isRevtColor = false,
  }: InputProps & { isRevtColor?: boolean } = $props();
  // static component is read-only, but we still use useArgValue to handle reactive updates from parent
  const arg = $derived(useArgValue<any>(data));
</script>

<div class={cn("relative flex h-7 w-full p-1 px-2", className)}>
  <span
    class={cn(
      "text-primary/80 truncate font-semibold",
      isRevtColor ? "text-white" : "text-primary",
      isDesc ? "text-xs" : "",
    )}
  >
    {arg.getLabel(arg.value)}
  </span>
</div>

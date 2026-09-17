<script lang="ts">
  import { type InputProps, useArgValue } from "$lib/components/arg/utils.svelte";
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

<!-- w-fit: a static value has no input box to fill, hugging the content lets
     the value column's justify-center center it, like the checkbox -->
<div class={cn("relative flex h-7 w-fit items-center p-1 px-2", className)}>
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

<script lang="ts">
  import { cn } from "$lib/utils";
  import type { LayoutProps } from "./types";

  let {
    data = $bindable(),
    parentWidth,
    InputComponent,
    handleEdit,
    handleReset,
    class: className,
  }: LayoutProps = $props();

  const isCompact = $derived(parentWidth && parentWidth < 420);
  const displayName = $derived.by(() => {
    // Show name if available
    if (data.name) {
      return data.name;
    }
    // Othersize show as {group_name}.{arg_name}
    return `${data.group || "<UnknownGroup>"}.${data.arg || "<UnknownArg>"}`;
  });
</script>

<div
  class={cn(
    "flex",
    // --- WIDE MODE ---
    !isCompact && "flex-row items-baseline justify-between gap-x-4",
    // --- COMPACT MODE ---
    isCompact && "flex-col gap-y-2",
    className,
  )}
>
  <div class="min-w-0 flex-1">
    <p class="font-medium">{displayName}</p>
    {#if data.help}
      <p class="text-muted-foreground mt-1 text-sm">{data.help}</p>
    {/if}
  </div>

  <div class={cn("flex justify-center", !isCompact && "w-9/20 max-w-50", isCompact && "w-full")}>
    <InputComponent bind:data {handleEdit} {handleReset} />
  </div>
</div>

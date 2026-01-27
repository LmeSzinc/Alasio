<script lang="ts">
  import { cn } from "$lib/utils";
  import { getArgName, type LayoutProps } from "./utils.svelte";
  import I18nText from "./I18nText.svelte";

  let {
    data = $bindable(),
    parentWidth,
    InputComponent,
    handleEdit,
    handleReset,
    class: className,
  }: LayoutProps = $props();

  const isCompact = $derived(parentWidth && parentWidth < 640);
  const displayName = $derived(getArgName(data));
</script>

<div class={cn("flex flex-col justify-center gap-y-1", className)}>
  <!-- Top row: name (left) and input (right) -->
  <div class="flex min-h-8 flex-row items-center justify-between gap-x-4">
    <!-- Top-left: name -->
    <div class="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
      <I18nText text={displayName} class="font-medium" />
    </div>

    <!-- Top-right: input -->
    <div class="flex w-9/20 max-w-50 justify-center">
      <InputComponent bind:data {handleEdit} {handleReset} />
    </div>
  </div>

  <!-- Bottom row: help (left) and placeholder (right) -->
  {#if data.help}
    <div class={cn("flex flex-row gap-x-4", isCompact && "flex-col")}>
      <!-- Bottom-left: help (spans full width in compact mode) -->
      <div class={cn("flex min-w-0 flex-col justify-center gap-0.5", !isCompact && "flex-1")}>
        <I18nText text={data.help} class="text-muted-foreground text-xs" />
      </div>

      <!-- Bottom-right: placeholder (hidden in compact mode) -->
      {#if !isCompact}
        <div class="w-9/20 max-w-50"></div>
      {/if}
    </div>
  {/if}
</div>

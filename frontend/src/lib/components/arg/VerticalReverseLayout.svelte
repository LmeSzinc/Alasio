<script lang="ts">
  import { cn } from "$lib/utils";
  import { getArgName, type LayoutProps } from "./utils.svelte";

  let {
    data = $bindable(),
    parentWidth,
    InputComponent,
    handleEdit,
    handleReset,
    class: className,
  }: LayoutProps = $props();

  const displayName = $derived(getArgName(data));
</script>

<div class={cn("flex flex-col gap-y-2", className)}>
  <!-- First row: name -->
  <div class="flex min-h-8 items-center">
    <p class="font-medium break-words">{displayName}</p>
  </div>

  <!-- Second row: input -->
  <div class="flex items-center">
    <InputComponent bind:data {handleEdit} {handleReset} />
  </div>

  <!-- Third row: help -->
  {#if data.help}
    <div class="flex items-center">
      <p class="text-muted-foreground text-sm break-words">{data.help}</p>
    </div>
  {/if}
</div>

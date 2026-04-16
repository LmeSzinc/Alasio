<script lang="ts">
  import { cn } from "$lib/utils";
  import { getArgName, type LayoutProps } from "./utils.svelte";
  import ToggleHelp from "./ToggleHelp.svelte";
  import I18nText from "./I18nText.svelte";

  let {
    data = $bindable(),
    parentWidth,
    InputComponent,
    isAdvanced = false,
    handleEdit,
    handleReset,
    class: className,
  }: LayoutProps = $props();

  const displayName = $derived(getArgName(data));

  let helpVisible = $state(false);
  const shouldFoldHelp = $derived(data.fold_help && !isAdvanced);
  const isHelpShown = $derived(!shouldFoldHelp || helpVisible);
</script>

<div class={cn("flex flex-col gap-y-2", className)}>
  <!-- First row: name -->
  <div class="flex flex-row items-center justify-start gap-x-1.5 overflow-hidden">
    <I18nText text={displayName} class="font-medium" />
    {#if shouldFoldHelp}
      <ToggleHelp bind:helpVisible />
    {/if}
  </div>

  <!-- Second row: help -->
  {#if data.help && isHelpShown}
    <div class="flex flex-col justify-center gap-0.5">
      <I18nText text={data.help} class="text-muted-foreground text-xs" />
    </div>
  {/if}

  <!-- Third row: input -->
  <div class="items-center">
    <InputComponent bind:data {handleEdit} {handleReset} />
  </div>
</div>

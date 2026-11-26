<script lang="ts">
  import { cn } from "$lib/utils";
  import AdvancedBadge from "./AdvancedBadge.svelte";
  import I18nText from "./I18nText.svelte";
  import ToggleHelp from "./ToggleHelp.svelte";
  import { type LayoutProps, getArgName, useVisible } from "./utils.svelte";

  let {
    data = $bindable(),
    parentWidth,
    InputComponent,
    isAdvanced = false,
    handleEdit,
    handleReset,
    onInvalidChange,
    class: className,
  }: LayoutProps = $props();

  const displayName = $derived(getArgName(data));

  // The help opens with the advanced settings of the card, and its own question
  // mark folds it on its own, see useVisible()
  const help = useVisible(
    () => !!data.fold_help,
    () => isAdvanced,
  );
</script>

<div class={cn("flex flex-col gap-y-2", className)}>
  <!-- First row: name -->
  <div class="flex flex-row items-center justify-start gap-x-1.5 overflow-hidden">
    <I18nText text={displayName} class="font-medium" />
    {#if data.advanced}
      <AdvancedBadge />
    {/if}
    {#if data.fold_help}
      <ToggleHelp bind:helpVisible={help.visible} />
    {/if}
  </div>

  <!-- Second row: input -->
  <div class="flex items-center">
    <InputComponent {data} {handleEdit} {handleReset} {onInvalidChange} />
  </div>

  <!-- Third row: help -->
  {#if data.help && help.shown}
    <div class="flex flex-col justify-center gap-0.5">
      <I18nText text={data.help} class="text-xs text-muted-foreground" />
    </div>
  {/if}
</div>

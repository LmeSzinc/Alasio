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
    ActionComponent,
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
  <!-- First row: name, with the optional row action on the right (e.g. the
       edit button of dt=filter-order); without one the row is unchanged -->
  <div class="flex flex-row items-center justify-between gap-x-4">
    <div class="flex min-w-0 flex-1 flex-row items-center gap-x-1.5 overflow-hidden">
      <I18nText text={displayName} class="font-medium" />
      {#if data.advanced}
        <AdvancedBadge />
      {/if}
      {#if data.fold_help}
        <ToggleHelp bind:helpVisible={help.visible} />
      {/if}
    </div>
    {#if ActionComponent}
      <div class="flex w-9/20 max-w-50 shrink-0 justify-center">
        <ActionComponent {data} {handleEdit} {handleReset} />
      </div>
    {/if}
  </div>

  <!-- Second row: help -->
  {#if data.help && help.shown}
    <div class="flex flex-col justify-center gap-0.5">
      <I18nText text={data.help} class="text-xs text-muted-foreground" />
    </div>
  {/if}

  <!-- Third row: input -->
  <div class="items-center">
    <InputComponent {data} {handleEdit} {handleReset} {onInvalidChange} />
  </div>
</div>

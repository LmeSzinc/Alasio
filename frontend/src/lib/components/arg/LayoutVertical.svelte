<script lang="ts">
  import { cn } from "$lib/utils";
  import I18nText from "./I18nText.svelte";
  import ToggleHelp from "./ToggleHelp.svelte";
  import { type LayoutProps, getArgName } from "./utils.svelte";

  let {
    data = $bindable(),
    parentWidth,
    InputComponent,
    ActionComponent,
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
  <!-- First row: name, with the optional row action on the right (e.g. the
       edit button of dt=filter-order); without one the row is unchanged -->
  <div class="flex flex-row items-center justify-between gap-x-4">
    <div class="flex min-w-0 flex-1 flex-row items-center gap-x-1.5 overflow-hidden">
      <I18nText text={displayName} class="font-medium" />
      {#if shouldFoldHelp}
        <ToggleHelp bind:helpVisible />
      {/if}
    </div>
    {#if ActionComponent}
      <div class="flex w-9/20 max-w-50 shrink-0 justify-center">
        <ActionComponent {data} {handleEdit} {handleReset} />
      </div>
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
    <InputComponent {data} {handleEdit} {handleReset} />
  </div>
</div>

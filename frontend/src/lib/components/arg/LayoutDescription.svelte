<script lang="ts">
  import { cn } from "$lib/utils";
  import AdvancedBadge from "./AdvancedBadge.svelte";
  import I18nText from "./I18nText.svelte";
  import { type LayoutProps, getArgName } from "./utils.svelte";

  let {
    data = $bindable(),
    InputComponent,
    handleEdit,
    handleReset,
    onInvalidChange,
    class: className,
  }: LayoutProps = $props();

  const displayName = $derived(getArgName(data));
</script>

<div class={cn("flex flex-row items-center gap-x-1.5", className)}>
  <div class="flex min-w-0 flex-1 flex-row items-center gap-x-1.5 overflow-hidden">
    <I18nText text={displayName} class="text-sm text-muted-foreground" />
    {#if data.advanced}
      <AdvancedBadge />
    {/if}
  </div>
  <div class="flex w-9/20 max-w-50 justify-center">
    <InputComponent {data} {handleEdit} {handleReset} {onInvalidChange} isDesc={true} />
  </div>
</div>

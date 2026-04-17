<script lang="ts">
  import { cn } from "$lib/utils";
  import { getArgName, type LayoutProps } from "./utils.svelte";
  import I18nText from "./I18nText.svelte";

  let { data = $bindable(), InputComponent, handleEdit, handleReset, class: className }: LayoutProps = $props();

  const displayName = $derived(getArgName(data));
</script>

<div class={cn("flex flex-row items-center gap-x-1.5", className)}>
  <I18nText text={displayName} class="text-muted-foreground flex-1 text-sm" />
  <div class="flex w-9/20 max-w-50 justify-center">
    <InputComponent bind:data {handleEdit} {handleReset} isDesc={true} />
  </div>
</div>

<script lang="ts">
  import { cn } from "$lib/utils";
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
  <I18nText text={displayName} class="flex-1 text-sm text-muted-foreground" />
  <div class="flex w-9/20 max-w-50 justify-center">
    <InputComponent {data} {handleEdit} {handleReset} {onInvalidChange} isDesc={true} />
  </div>
</div>

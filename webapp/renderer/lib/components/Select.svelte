<script lang="ts">
  import * as Select from "$lib/components/ui/select";
  import { cn } from "$lib/utils";

  interface SelectOption {
    value: string;
    label: string;
  }

  let {
    value = $bindable(),
    options,
    onValueChange,
    id,
    class: className,
  }: {
    value: string | undefined;
    options: SelectOption[];
    onValueChange?: (value: string | undefined) => void;
    id?: string;
    class?: string;
  } = $props();

  let triggerEl: HTMLElement | null = $state(null);

  // Find the label for the current selected value
  const triggerLabel = $derived(options.find((option) => option.value === value)?.label ?? options[0]?.label ?? "");

  // Remove focus from the trigger after selection or clicking it to close,
  // so the focus ring does not persist (same as the arginput Select).
  function onOpenChangeComplete(isOpen: boolean) {
    if (!isOpen) {
      setTimeout(() => {
        triggerEl?.blur();
      }, 0);
    }
  }
</script>

<div class={cn("w-full", className)}>
  <Select.Root type="single" bind:value {onValueChange} {onOpenChangeComplete}>
    <Select.Trigger
      {id}
      class={cn(
        "group relative h-9! w-full border-0 bg-card p-1 pl-2 shadow-none dark:bg-card",
        "focus:shadow-none",
        "focus:z-10 focus:ring-2 focus:ring-ring focus:ring-offset-5 focus:ring-offset-background",
        "transition-shadow duration-200",
      )}
      bind:ref={triggerEl}
    >
      <span class="flex-1 truncate text-left">{triggerLabel}</span>
      <!-- Draw bottom border with peer -->
      <div
        class={cn(
          "absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200 group-focus:border-foreground/35",
          "border-primary",
        )}
      ></div>
    </Select.Trigger>

    <Select.Content>
      <Select.Group>
        {#each options as option (option.value)}
          <Select.Item value={option.value}>{option.label}</Select.Item>
        {/each}
      </Select.Group>
    </Select.Content>
  </Select.Root>
</div>

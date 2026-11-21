<script lang="ts">
  import { type InputProps, useArgValue } from "$lib/components/arg/utils.svelte";
  import * as Select from "$lib/components/ui/select";
  import { cn } from "$lib/utils";

  let { data = $bindable(), class: className, handleEdit, isDesc = false }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let triggerEl: HTMLElement | null = $state(null);

  // Get options from data.option or use empty array as fallback
  const options = $derived(data.option || []);
  // Convert value to string for Select component compatibility
  const stringValue = $derived(arg.value !== undefined && arg.value !== null ? String(arg.value) : undefined);

  // Find the label for the current selected value
  const triggerContent = $derived(() => {
    if (arg.value !== undefined && arg.value !== null) {
      return arg.getLabel(arg.value);
    }
    return "Select an option";
  });

  function onValueChange(value: string | undefined) {
    if (value !== undefined) {
      // Convert back to the original type if needed
      // If the original option is a number, convert the string back to number
      let parsedValue: any = value;
      const originalOption = options.find((opt: any) => String(opt) === value);
      if (originalOption !== undefined) {
        parsedValue = originalOption;
      }
      // Update the arg value
      arg.value = parsedValue;
      // Immediately trigger the submission logic
      arg.submit(handleEdit);
      // Remove focus from the trigger after selection
      // This prevents the focus ring from persisting after selection
      setTimeout(() => {
        triggerEl?.blur();
      }, 0);
    }
  }
  function onOpenChangeComplete(isOpen: boolean) {
    // Remove focus from the trigger after clicking it to close
    if (!isOpen && triggerEl) {
      setTimeout(() => {
        triggerEl?.blur();
      }, 0);
    }
  }
</script>

<div class={cn("w-full", className)}>
  <Select.Root type="single" value={stringValue} {onValueChange} {onOpenChangeComplete}>
    <Select.Trigger
      class={cn(
        "group relative h-7! w-full border-0 bg-card p-1 pl-2 shadow-none dark:bg-card",
        "focus:shadow-none",
        "focus:z-10 focus:ring-2 focus:ring-ring focus:ring-offset-5 focus:ring-offset-background",
        // The shadcn trigger draws a wider translucent ring on keyboard focus
        // (focus-visible:ring-3 ring-ring/50); the arg inputs draw the plain
        // theme color ring, so the row keeps that one.
        "focus-visible:ring-2 focus-visible:ring-ring",
        "transition-shadow duration-200",
      )}
      bind:ref={triggerEl}
    >
      <span class="flex-1 truncate text-left">
        {triggerContent()}
      </span>
      <!-- Draw bottom border with peer -->
      <div
        class={cn(
          "absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200 group-focus:border-foreground/35",
          isDesc ? "border-transparent group-hover:border-primary" : "border-primary",
        )}
      ></div>
    </Select.Trigger>

    <Select.Content>
      <Select.Group>
        {#if options.length > 0}
          {#each options as option (option)}
            {@const label = arg.getLabel(option)}
            <Select.Item value={String(option)} {label}>
              {label}
            </Select.Item>
          {/each}
        {:else}
          <Select.Label>No options available</Select.Label>
        {/if}
      </Select.Group>
    </Select.Content>
  </Select.Root>
</div>

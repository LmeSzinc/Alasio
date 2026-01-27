<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import * as Select from "$lib/components/ui/select";
  import { cn } from "$lib/utils";

  let { data = $bindable(), class: className, handleEdit }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let triggerEl: HTMLElement | null = $state(null);

  // Get options from data.option or use empty array as fallback
  const options = $derived(data.option || []);
  // Get i18n labels dict if available (key: option value, value: display text)
  const optionI18n = $derived(data.option_i18n || {});
  // Convert value to string for Select component compatibility
  const stringValue = $derived(arg.value !== undefined && arg.value !== null ? String(arg.value) : undefined);

  // Get the display label for a given option value
  function getLabel(optionValue: any): string {
    // If option_i18n exists and has the key, use it
    if (optionI18n[optionValue]) {
      return optionI18n[optionValue];
    }
    // Otherwise use the option value itself as the label
    return String(optionValue);
  }

  // Find the label for the current selected value
  const triggerContent = $derived(() => {
    if (arg.value !== undefined) {
      return getLabel(arg.value);
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
        "group bg-card dark:bg-card relative h-7! w-full border-0 p-1 pl-2 shadow-none",
        "focus:shadow-none",
        "focus:ring-ring focus:ring-2 focus:ring-offset-5",
        "transition-shadow duration-200",
      )}
      bind:ref={triggerEl}
    >
      {triggerContent()}
      <!-- Draw bottom border with peer -->
      <div
        class="border-primary group-focus:border-foreground/35 absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200"
      ></div>
    </Select.Trigger>

    <Select.Content>
      <Select.Group>
        {#if options.length > 0}
          {#each options as option (option)}
            {@const label = getLabel(option)}
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

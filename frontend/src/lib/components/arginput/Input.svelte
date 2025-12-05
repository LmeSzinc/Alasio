<script lang="ts">
  import {
    useArgValue,
    validateByConstraints,
    validateByDataType,
    type InputProps,
  } from "$lib/components/arg/utils.svelte";
  import { Help } from "$lib/components/ui/help";
  import { Input } from "$lib/components/ui/input";
  import { cn } from "$lib/utils";
  import Reset from "./_Reset.svelte";

  let { data = $bindable(), class: className, handleEdit, handleReset }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let inputEl: HTMLInputElement | null = $state(null);

  // Control when to show the error message (e.g. hide while typing)
  let showError = $state(false);

  // Derived validation error that reacts to language changes (t) and value changes
  const validationError = $derived.by(() => {
    const value = arg.value;
    // Skip validation for empty values
    if (!value) return null;

    // Validate by data type
    const typeError = validateByDataType(value, data.dt);
    if (typeError) return typeError;

    // Validate by constraints
    const constraintError = validateByConstraints(value, data);
    if (constraintError) return constraintError;

    return null;
  });

  // The actual error message to display
  const errorMessage = $derived(showError ? validationError : null);

  // Helper to check validity for submission (non-reactive)
  function isValid(value: string) {
    if (!value) return true;
    return !validateByDataType(value, data.dt) && !validateByConstraints(value, data);
  }

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Hide error while typing
    showError = false;

    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => {
      // Show error after debounce
      showError = true;

      // Validate before submitting
      if (isValid(arg.value)) {
        arg.submit(handleEdit);
        // Remove focus after submission to prevent focus ring from persisting
        setTimeout(() => {
          inputEl?.blur();
        }, 0);
      }
    }, 3000);
  }

  function onBlur() {
    // To prevent double-firing, clear any pending timer
    clearTimeout(debounceTimer);

    // Show error on blur
    showError = true;

    // Validate before submitting
    if (isValid(arg.value)) {
      // Immediately trigger the edit callback when the user leaves the input
      arg.submit(handleEdit);
    }
  }

  function onReset() {
    // Hide error on reset
    showError = false;
    // Trigger the provided reset callback
    arg.reset(handleReset);
  }
</script>

<div class={cn("flex-col", className)}>
  <div class="relative flex w-full items-center">
    <!-- Primary color bottom border -->
    <!-- On focus, gray bottom border and primary color rounded ring -->
    <Input
      type="text"
      class={cn(
        "bg-card dark:bg-card peer border-0 p-1 pr-7 pl-2 shadow-none",
        "focus-visible:shadow-none",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-5",
        "transition-shadow duration-200",
        errorMessage && "ring-destructive ring-2 ring-offset-5",
      )}
      bind:value={arg.value}
      bind:ref={inputEl}
      oninput={onInput}
      onblur={onBlur}
    />

    <!-- Draw bottom border with peer -->
    <div
      class="border-primary peer-focus-visible:border-foreground/35 absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200"
    ></div>

    <!-- Reset button is always visible to allow resetting to a default value -->
    <Reset {onReset} class="absolute right-0" />
  </div>

  <!-- Error message display -->
  {#if errorMessage}
    <Help variant="error" class="mt-4">
      {errorMessage}
    </Help>
  {/if}
</div>

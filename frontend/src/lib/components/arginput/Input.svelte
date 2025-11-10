<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { Input } from "$lib/components/ui/input";
  import { cn } from "$lib/utils";
  import Reset from "./_Reset.svelte";

  let { data = $bindable(), class: className, handleEdit, handleReset }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let inputEl: HTMLInputElement | null = $state(null);

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => {
      arg.submit(handleEdit);
      // Remove focus after submission to prevent focus ring from persisting
      setTimeout(() => {
        inputEl?.blur();
      }, 0);
    }, 3000);
  }
  function onBlur() {
    // To prevent double-firing, clear any pending timer
    clearTimeout(debounceTimer);
    // Immediately trigger the edit callback when the user leaves the input
    arg.submit(handleEdit);
  }
  function onReset() {
    // Trigger the provided reset callback
    handleReset?.(data);
  }
</script>

<div class={cn("relative flex w-full items-center", className)}>
  <!-- Primary color bottom border -->
  <!-- On focus, gray bottom border and primary color rounded ring -->
  <Input
    type="text"
    class={cn(
      "peer bg-card dark:bg-card border-0 p-1 pr-7 pl-2 shadow-none",
      "focus-visible:shadow-none",
      "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-5",
      "transition-shadow duration-200",
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

<script lang="ts">
  import type { InputProps } from "$lib/components/arg/types";
  import { useArgValue } from "$lib/components/arg/useArgValue.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { cn } from "$lib/utils";
  import { X } from "@lucide/svelte";

  let { data = $bindable(), class: className, handleEdit, handleReset }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => arg.submit(handleEdit), 1000);
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
  <Input
    type="text"
    class={cn(
      // --- CORE CHANGES ---
      // 1. Remove all native borders to ensure consistent box-sizing.
      "bg-card dark:bg-card border-0 shadow-none",

      // 2. Create the "bottom border" using a box-shadow.
      //    Syntax: `shadow-[offsetX offsetY blur spread color]`
      //    `0 1px 0 0` creates a sharp 1px line directly below the element.
      //    `hsl(var(--primary))` uses your theme's primary color variable.
      "shadow-[0_1px_0_hsl(var(--primary))]1",

      // 3. On focus, REMOVE our custom shadow and let the rings take over.
      //    This creates the "swap" effect from line to rings without layout shift.
      "focus-visible:shadow-none",

      // --- Unchanged from before ---
      "pr-8 pl-2", // Keep padding for the X button
      "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2", // Standard rings styles
      "transition-shadow duration-200", // BONUS: Add a smooth transition
    )}
    bind:value={arg.value}
    oninput={onInput}
    onblur={onBlur}
  />

  <!-- Reset button is always visible to allow resetting to a default value -->
  <Button variant="ghost" size="icon" class="absolute right-0 h-7 w-7" onclick={onReset} aria-label="Reset value">
    <X class="h-4 w-4" />
  </Button>
</div>

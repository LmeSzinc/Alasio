<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Textarea } from "$lib/components/ui/textarea";
  import { cn } from "$lib/utils";
  import { RotateCcw } from "@lucide/svelte";

  let { data = $bindable(), class: className, handleEdit, handleReset }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => arg.submit(handleEdit), 3000);
  }
  function onBlur() {
    // To prevent double-firing, clear any pending timer
    clearTimeout(debounceTimer);
    // Immediately trigger the edit callback when the user leaves the textarea
    arg.submit(handleEdit);
  }
  function onReset() {
    // Trigger the provided reset callback
    handleReset?.(data);
  }
</script>

<div class={cn("relative flex w-full items-start", className)}>
  <Textarea
    class={cn(
      "peer bg-accent resize-y p-2 pr-8 pl-2 font-mono  shadow-none",
      "focus-visible:shadow-none",
      "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-5",
      "transition-shadow duration-200",
      "min-h-[80px]",
    )}
    bind:value={arg.value}
    oninput={onInput}
    onblur={onBlur}
  />

  <!-- Reset button is always visible to allow resetting to a default value -->
  <Button variant="ghost" size="icon" class="absolute top-1 right-1 h-6 w-6" onclick={onReset} aria-label="Reset value">
    <RotateCcw class="text-muted-foreground opacity-50 size-3" />
  </Button>
</div>

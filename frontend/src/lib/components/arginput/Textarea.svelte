<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { Textarea } from "$lib/components/ui/textarea";
  import { cn } from "$lib/utils";
  import Reset from "./_Reset.svelte";

  let { data = $bindable(), class: className, handleEdit, handleReset }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  let textareaEl: HTMLTextAreaElement | null = $state(null);

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => {
      arg.submit(handleEdit);
      // Remove focus after submission to prevent focus ring from persisting
      setTimeout(() => {
        textareaEl?.blur();
      }, 0);
    }, 3000);
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
    bind:ref={textareaEl}
    oninput={onInput}
    onblur={onBlur}
  />

  <!-- Reset button is always visible to allow resetting to a default value -->
  <Reset {onReset} class="hover:bg-card absolute top-1 right-1" />
</div>

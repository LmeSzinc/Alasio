<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import { cn } from "$lib/utils";

  let { data = $bindable(), class: className, handleEdit }: InputProps = $props();
  const arg = $derived(useArgValue<boolean>(data));

  function onChange() {
    // 1. Immediately trigger the submission logic, passing the specific
    //    `handleEdit` function for this component instance.
    arg.submit(handleEdit);
  }
</script>

<Checkbox class={cn("size-4.5", className)} iconStrokeWidth={3.5} bind:checked={arg.value} onCheckedChange={onChange} />

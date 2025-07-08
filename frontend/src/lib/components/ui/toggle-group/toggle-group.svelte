<script lang="ts" module>
  import { getContext, setContext } from "svelte";
  import type { ToggleVariants } from "$lib/components/ui/toggle/index.js";
  export function setToggleGroupCtx(props: ToggleVariants) {
    setContext("toggleGroup", props);
  }

  export function getToggleGroupCtx() {
    return getContext<ToggleVariants>("toggleGroup");
  }
</script>

<script lang="ts">
  import { ToggleGroup as ToggleGroupPrimitive } from "bits-ui";
  import { cn } from "$lib/utils.js";
  import { untrack } from "svelte";

  type $$Props = Omit<ToggleGroupPrimitive.RootProps, "onValueChange"> &
    ToggleVariants

  // MODIFIED: If type is "single", toggle group must select one item
  // no empty selection, no multi selection
  let {
    ref = $bindable(null),
    value = $bindable(),
    class: className,
    size = "default",
    variant = "default",
    // catch additional `type`
    type = "single",
    ...restProps
  }: $$Props = $props();

  // We need a separate state to track the "last valid" value.
  let lastValidValue = $state(value);

  $effect(() => {
    // This effect runs whenever the bound `value` changes, either by
    // user interaction or from the parent.
    const currentValue = value;

    // The Logic: If the type is single, and the new value is "" (deselection),
    // and the last known valid value was not "", then it's an invalid change.
    if (type === "single" && currentValue === "" && lastValidValue !== "") {
      // Correct the invalid change.
      // We write directly back to the `value` bindable prop.
      // This will update the parent component's state and flow back down,
      // restoring the selection.
      value = lastValidValue;
    } else {
      // The change was valid, so we update our "last valid" tracker.
      // We use untrack to prevent an infinite loop. We only want this
      // part of the code to run when `currentValue` changes, not when
      // `lastValidValue` itself changes.
      untrack(() => {
        lastValidValue = currentValue;
      });
    }
  });

  setToggleGroupCtx({
    variant,
    size,
  });
</script>

<!--
  Discriminated Unions + Destructing (required for bindable) do not
  get along, so we shut typescript up by casting `value` to `never`.
  -->
<ToggleGroupPrimitive.Root
  bind:value={value as never}
  bind:ref
  {type}
  data-slot="toggle-group"
  data-variant={variant}
  data-size={size}
  class={cn(
    "group/toggle-group flex w-fit items-center rounded-md data-[variant=outline]:shadow-xs",
    className
  )}
  {...restProps}
/>

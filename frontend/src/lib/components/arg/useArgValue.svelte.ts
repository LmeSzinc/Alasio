import type { ArgData, InputProps } from "$lib/components/arg/types";
import { untrack } from "svelte";

/**
 * A Svelte 5 composable function (hook) to manage the state of an argument input.
 * It handles local state, synchronization with parent props, optimistic updates,
 * and conditional submission.
 *
 * @param data The ArgData object passed from the parent component. For optimistic
 *             updates to work, the parent must use `bind:data`.
 * @returns An object containing the current value and a submit function.
 */
export function useArgValue<T>(data: ArgData) {
  // 1. LOCAL STATE: Create a local, reactive state for the input's value.
  //    This `currentValue` is what the UI component will bind to (e.g., `bind:value`).
  //    It's initialized from the `data` prop.
  let currentValue = $state(data.value as T);

  // 2. PROP SYNCHRONIZATION: Use an effect to keep the local state in sync
  //    with the `data` prop from the parent. This ensures that if the data
  //    is updated externally, the UI reflects the change.
  $effect.pre(() => {
    // `untrack()` prevents this effect from re-running when `currentValue` changes.
    // We only want this effect to trigger when the incoming `data.value` prop changes.
    if (data.value !== untrack(() => currentValue)) {
      currentValue = data.value;
    }
  });

  /**
   * Submits the current value if it has changed.
   * This function performs an optimistic update and then calls an optional handler.
   *
   * @param handleEdit An optional callback function to execute the side effect,
   *                   like an API call.
   */
  function submit(handleEdit?: InputProps["handleEdit"]) {
    // 3. DIRTY CHECK: Only proceed if the local value is different from the
    //    last known value from the prop. This prevents redundant operations.
    if (currentValue !== data.value) {
      // a. OPTIMISTIC UPDATE: Directly mutate the `data` prop's value.
      //    This is a "controlled mutation" that Svelte 5 allows and understands
      //    when the parent uses `bind:data`. It makes the UI feel instantaneous.
      //    Svelte translates this mutation into an update of the parent's state.
      data.value = currentValue;

      // b. EXECUTE SIDE EFFECT: Call the provided `handleEdit` callback with the
      //    new data object. This is where the API call would happen.
      handleEdit?.(data);
    }
  }

  // 4. RETURN API: Expose the local value and the submit function to the component.
  //    The getter/setter pair allows the component to use `bind:value={arg.value}`.
  return {
    get value() {
      return currentValue;
    },
    set value(newValue: T) {
      currentValue = newValue;
    },
    submit,
  };
}

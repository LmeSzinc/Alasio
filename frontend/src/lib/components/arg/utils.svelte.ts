import type { Component } from "svelte";

export type ArgData = {
  task: string;
  group: string;
  arg: string;
  dt: string;
  value: any;
  name?: string;
  help?: string;
  layout?: string;
  [key: string]: any;
};

export type InputProps = {
  data: ArgData;
  class?: string;
  handleEdit?: (data: ArgData) => void;
  handleReset?: (data: ArgData) => void;
};

export type LayoutProps = InputProps & {
  parentWidth?: number;
  InputComponent: Component<InputProps>;
};

export type ArgProps = InputProps & {
  parentWidth?: number;
};

export function getArgName(arg: ArgData) {
  // Show name if available
  if (arg.name) {
    return arg.name;
  }
  // Othersize show as {group_name}.{arg_name}
  return `${arg.group || "<UnknownGroup>"}.${arg.arg || "<UnknownArg>"}`;
}

/**
 * A Svelte 5 composable function (hook) to manage the state of an argument input.
 * It handles local state, synchronization with parent props, optimistic updates,
 * and conditional submission.
 *
 * Note that you should always deco with $derived because this is an enclosure
 * function that arg won't get update if the entire data changed.
 * Usage:
 *    const arg = $derived(useArgValue<boolean>(data));
 *    <Checkbox bind:checked={arg.value} onCheckedChange={onChange} />
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

  /**
   * Submits the current value if it has changed.
   * This function performs an optimistic update and then calls an optional handler.
   *
   * @param handleEdit An optional callback function to execute the side effect,
   *                   like an API call.
   */
  function submit(handleEdit?: InputProps["handleEdit"]) {
    // 2. DIRTY CHECK: Only proceed if the local value is different from the
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

  // 3. RETURN API: Expose the local value and the submit function to the component.
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

import { t } from "$lib/i18n";
import type { Component } from "svelte";

export type ArgData = {
  task: string;
  group: string;
  arg: string;
  dt: string;
  value: any;
  name?: string;
  help?: string;

  advanced?: boolean;
  /**
   * Layout style, layout is determined according to `dt` by frontend (see $lib/component/arg/Arg.svelte)
   * Most `dt` are show as horizontal, some are special e.g. dt=textarea is vertical
   * If you need custom layout, set this value
   * 1. "hori" for horizontal layout
   *    - {name} {input}
   *    - {help} (placeholder)
   *    placeholder disappear when component in compact mode
   * 2. "vert" for vertical layout with 3 rows:
   *    - {name}
   *    - {help}
   *    - {input}
   * 3. "vert-rev" for reversed vertical layout with 3 rows:
   *    - {name}
   *    - {input}
   *    - {help}
   */
  layout?: "hori" | "vert" | "vert-rev";

  option?: any[];
  option_i18n?: Record<any, string>;
  // Msgspec constraints
  // https://jcristharif.com/msgspec/constraints.html

  // The annotated value must be greater than gt.
  gt?: number;
  // The annotated value must be greater than or equal to ge.
  ge?: number;
  // The annotated value must be less than lt.
  lt?: number;
  // The annotated value must be less than or equal to le.
  le?: number;
  // The annotated value must be a multiple of multiple_of.
  multiple_of?: number;
  // A regex pattern that the annotated value must match against.
  pattern?: string;
  // The annotated value must have a length greater than or equal to min_length.
  min_length?: number;
  // The annotated value must have a length less than or equal to max_length.
  max_length?: number;
  // Configures the timezone-requirements for annotated datetime/time types.
  tz?: boolean;
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
  $effect(() => {
    // reset current value if remote changed
    currentValue = data.value;
  });

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

  function reset(handleReset?: InputProps["handleReset"]) {
    // Set data.value, so we can rollback current value later
    data.value = currentValue;
    handleReset?.(data);
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
    reset,
  };
}

/**
 * Validate input value based on data type (dt field) and convert to appropriate type
 *
 * @param value The input value to validate
 * @param dt Data type from ArgData (e.g., "input", "input-int", "input-float")
 * @returns Object containing the validated/converted value and error message (if any)
 */
export function validateByDataType(
  value: string,
  dt: string
): { value: any; error: string | null } {
  // For input-int, parse and validate integer
  if (dt === "input-int") {
    // If already a number, check if it's an integer
    if (typeof value === "number") {
      if (Number.isInteger(value)) {
        return { value, error: null };
      } else {
        return { value, error: t.Input.InvalidInteger() };
      }
    }
    
    // Convert to string if needed and parse
    const stringValue = String(value);
    const parsedValue = parseInt(stringValue, 10);
    // Check if parsing resulted in NaN or if the string representation doesn't match
    // This catches cases like "123abc" where parseInt would return 123
    if (isNaN(parsedValue) || parsedValue.toString() !== stringValue.trim()) {
      return { value, error: t.Input.InvalidInteger() };
    }
    return { value: parsedValue, error: null };
  }

  // For input-float, parse and validate float
  if (dt === "input-float") {
    // If already a number, return it
    if (typeof value === "number" && !isNaN(value)) {
      return { value, error: null };
    }
    
    // Convert to string if needed and parse
    const stringValue = String(value);
    const parsedValue = parseFloat(stringValue);
    // Check if parsing resulted in NaN
    if (isNaN(parsedValue)) {
      return { value, error: t.Input.InvalidFloat() };
    }
    return { value: parsedValue, error: null };
  }

  // For regular input, return as-is
  return { value, error: null };
}

/**
 * Validate input value based on constraints from ArgData
 *
 * @param value The input value to validate (can be string, number, etc.)
 * @param data ArgData object containing constraint fields
 * @returns Error message string if validation fails, null if valid
 */
export function validateByConstraints(value: any, data: ArgData): string | null {
  // Convert value to number for numeric constraints (if not already a number)
  const numValue = typeof value === "number" ? value : parseFloat(value);
  const isNumeric = !isNaN(numValue);

  // Check gt (greater than) constraint
  if (data.gt !== undefined && isNumeric) {
    if (numValue <= data.gt) {
      return t.Input.GreaterThan({ value: data.gt });
    }
  }

  // Check ge (greater than or equal) constraint
  if (data.ge !== undefined && isNumeric) {
    if (numValue < data.ge) {
      return t.Input.GreaterThanOrEqual({ value: data.ge });
    }
  }

  // Check lt (less than) constraint
  if (data.lt !== undefined && isNumeric) {
    if (numValue >= data.lt) {
      return t.Input.LessThan({ value: data.lt });
    }
  }

  // Check le (less than or equal) constraint
  if (data.le !== undefined && isNumeric) {
    if (numValue > data.le) {
      return t.Input.LessThanOrEqual({ value: data.le });
    }
  }

  // Check multiple_of constraint
  if (data.multiple_of !== undefined && isNumeric) {
    // Use modulo to check if value is a multiple
    // Handle floating point precision issues
    const remainder = numValue % data.multiple_of;
    if (Math.abs(remainder) > 1e-10 && Math.abs(remainder - data.multiple_of) > 1e-10) {
      return t.Input.MultipleOf({ value: data.multiple_of });
    }
  }

  // Check pattern constraint (regex)
  if (data.pattern !== undefined) {
    try {
      const regex = new RegExp(data.pattern);
      const stringValue = typeof value === "string" ? value : String(value);
      if (!regex.test(stringValue)) {
        return t.Input.PatternMismatch();
      }
    } catch (e) {
      // Invalid regex pattern, skip validation
      console.warn(`Invalid regex pattern: ${data.pattern}`, e);
    }
  }

  // Check min_length constraint
  if (data.min_length !== undefined) {
    const stringValue = typeof value === "string" ? value : String(value);
    if (stringValue.length < data.min_length) {
      return t.Input.MinLength({ value: data.min_length });
    }
  }

  // Check max_length constraint
  if (data.max_length !== undefined) {
    const stringValue = typeof value === "string" ? value : String(value);
    if (stringValue.length > data.max_length) {
      return t.Input.MaxLength({ value: data.max_length });
    }
  }

  // All validations passed
  return null;
}

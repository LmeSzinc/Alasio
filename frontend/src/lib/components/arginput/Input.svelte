<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import {
    type InputProps,
    useArgValue,
    validateByConstraints,
    validateByDataType,
  } from "$lib/components/arg/utils.svelte";
  import { Help } from "$lib/components/ui/help";
  import { Input } from "$lib/components/ui/input";
  import { cn } from "$lib/utils";
  import Reset from "./_Reset.svelte";
  import { formatToLocal, parseToUTC, tzOffset } from "./dateutils.svelte";

  let {
    data = $bindable(),
    class: className,
    handleEdit,
    handleReset,
    isDesc = false,
    onInvalidChange,
  }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  // --- Datetime handling ---
  const isDatetime = $derived(data.dt === "datetime");

  // --- Box style ---
  // dt="input-line" takes the box of a textarea but is edited by an <input>,
  // so the value is one line and a newline is stripped by the input itself.
  // The desc layout shows plain text, it drops the box like it drops the line.
  const isBox = $derived(data.dt === "input-line" && !isDesc);

  // Local display value for datetime
  let displayValue = $state("");
  // Pending reset flag: set on mousedown (fires before blur), checked in onBlur to prevent
  // unwanted submit(handleEdit) when user clicks the reset button.
  let _pendingReset = $state(false);
  // Snapshot of displayValue at focus time; used on blur to skip submit when nothing changed
  let _focusDisplayValue = $state("");

  // Getter/setter for the input element to bind to
  const inputValue = {
    get val() {
      return isDatetime ? displayValue : arg.value;
    },
    set val(newValue: string) {
      if (isDatetime) {
        displayValue = newValue;
      } else {
        arg.value = newValue;
      }
    },
  };

  // Sync internal value to display value
  $effect(() => {
    if (isDatetime) {
      const currentArgValue = arg.value;
      untrack(() => {
        const local = formatToLocal(currentArgValue);
        if (local !== displayValue) {
          displayValue = local;
        }
      });
    }
  });

  let inputEl: HTMLInputElement | null = $state(null);

  // Control when to show the error message (e.g. hide while typing)
  let showError = $state(false);

  // Derived validation error that reacts to language changes (t) and value changes
  const validationError = $derived.by(() => {
    let value = arg.value;
    // Skip validation for empty values
    if (!value) return null;

    // Validate by data type and get converted value
    const typeValidation = validateByDataType(value, data.dt);
    if (typeValidation.error) return typeValidation.error;
    value = typeValidation.value;

    // Validate by constraints (use converted value for numeric types)
    const constraintError = validateByConstraints(value, data);
    if (constraintError) return constraintError;

    // Update arg.value if validation passed
    if (!typeValidation.error) {
      untrack(() => {
        arg.value = value;
      });
    }
    return null;
  });

  // The actual error message to display
  const errorMessage = $derived(showError ? validationError : null);

  // The text being typed lives inside this input and is only committed to the
  // arg once it passes validation, so a form around the input can not see a
  // rejected text in the arg: the validity is reported instead.
  //
  // Only the validity is tracked: the report itself runs untracked, so a caller
  // that stores what it hears does not feed back into this effect (which would
  // re-run on every store and report again, forever). A caller therefore has to
  // treat a repeated report as the same state, and the ones here do.
  $effect(() => {
    const invalid = errorMessage !== null;
    untrack(() => onInvalidChange?.(invalid));
  });

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Hide error while typing
    showError = false;

    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => {
      // For datetime, convert displayValue to UTC before validation/submission
      if (isDatetime) {
        arg.value = parseToUTC(displayValue);
      }

      // Show error after debounce
      showError = true;

      // Validate before submitting
      if (!errorMessage) {
        arg.submit(handleEdit);
        // Remove focus after submission to prevent focus ring from persisting
        setTimeout(() => {
          inputEl?.blur();
        }, 0);
      }
    }, 3000);
  }

  function onFocus() {
    // Snapshot the display value to detect no-op blur later
    if (isDatetime) {
      _focusDisplayValue = displayValue;
    }
  }

  function onBlur() {
    // If user just clicked the reset button (mousedown fired before blur), skip submit
    if (_pendingReset) {
      _pendingReset = false;
      return;
    }

    // To prevent double-firing, clear any pending timer
    clearTimeout(debounceTimer);

    if (isDatetime) {
      arg.value = parseToUTC(displayValue);
      // Skip submit if the display value hasn't changed since focus.
      // Without this guard, parseToUTC(formatToLocal(x)) produces a different
      // string than x (e.g. ".000Z" vs "Z"), making submit's dirty check
      // always fire even when nothing was edited.
      if (displayValue === _focusDisplayValue) {
        return;
      }
    }

    // Show error on blur
    showError = true;

    // Validate before submitting
    if (!errorMessage) {
      // Immediately trigger the edit callback when the user leaves the input
      arg.submit(handleEdit);
    }
  }

  function onReset() {
    // Clear pending reset flag
    _pendingReset = false;
    // Hide error on reset
    showError = false;
    // Trigger the provided reset callback
    arg.reset(handleReset);
  }
</script>

<div class={cn("group w-full", className)}>
  <div class="relative flex w-full items-center focus-within:z-10">
    <!-- dt="input": primary color bottom border, on focus a gray bottom
         border and a primary color rounded ring. dt="input-line" draws the whole
         box, like the textarea of dt="textarea". -->
    <Input
      type="text"
      class={cn(
        // dt="input" is a flat row with a bottom accent line instead of a border,
        // dt="input-line" keeps the border and takes the textarea box colors
        "peer truncate shadow-none",
        isBox ? "bg-accent font-mono" : "border-0 bg-card p-1 px-2 dark:bg-card",
        isDatetime ? "pr-12 focus-visible:pr-12" : "focus-visible:pr-6",
        isDesc ? "py-0 text-muted-foreground" : "",
        "focus-visible:shadow-none",
        "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-5 focus-visible:ring-offset-background",
        errorMessage && "ring-2 ring-destructive ring-offset-5 ring-offset-background",
      )}
      bind:value={inputValue.val}
      bind:ref={inputEl}
      onfocus={onFocus}
      oninput={onInput}
      onblur={onBlur}
      aria-invalid={errorMessage !== null}
    />

    <!-- Draw bottom border with peer, the box of dt="input-line" has all 4 sides -->
    {#if !isBox}
      <div
        class={cn(
          "absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200 peer-focus-visible:border-foreground/35",
          isDesc ? "border-transparent group-hover:border-primary" : "border-primary",
        )}
      ></div>
    {/if}

    <!-- Reset button is visible only on focus -->
    <div class="absolute right-1 flex items-center group-focus-within:right-0">
      {#if isDatetime}
        <span class="pointer-events-none text-xs text-muted-foreground select-none">{tzOffset}</span>
      {/if}
      <div
        class={cn(
          "flex items-center justify-center overflow-hidden transition-all duration-200",
          "w-0 group-focus-within:w-4",
        )}
      >
        <Reset
          {onReset}
          onmousedown={() => (_pendingReset = true)}
          class="opacity-0 transition-opacity duration-200 group-focus-within:opacity-100"
        />
      </div>
    </div>
  </div>

  <!-- Error message display -->
  {#if errorMessage}
    <Help variant="error" class="mt-4">
      {errorMessage}
    </Help>
  {/if}
</div>

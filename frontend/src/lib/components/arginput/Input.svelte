<script lang="ts">
  import {
    useArgValue,
    validateByConstraints,
    validateByDataType,
    type InputProps,
  } from "$lib/components/arg/utils.svelte";
  import { DEFAULT_TIME, DEFAULT_TIME_DISPLAY } from "$lib/components/dashboard/utils";
  import { Help } from "$lib/components/ui/help";
  import { Input } from "$lib/components/ui/input";
  import { cn } from "$lib/utils";
  import { untrack } from "svelte";
  import Reset from "./_Reset.svelte";

  let { data = $bindable(), class: className, handleEdit, handleReset, isDesc = false }: InputProps = $props();
  const arg = $derived(useArgValue<string>(data));

  // --- Datetime handling ---
  const isDatetime = $derived(data.dt === "datetime");
  const tzOffset = (() => {
    const offset = -new Date().getTimezoneOffset();
    const sign = offset >= 0 ? "+" : "-";
    const absOffset = Math.abs(offset);
    const h = String(Math.floor(absOffset / 60)).padStart(2, "0");
    const m = String(absOffset % 60).padStart(2, "0");
    return `${sign}${h}:${m}`;
  })();

  function formatToLocal(isoStr: any): string {
    if (isoStr === DEFAULT_TIME) return DEFAULT_TIME_DISPLAY;
    if (typeof isoStr !== "string" || !isoStr) return String(isoStr || "");
    const date = new Date(isoStr);
    if (isNaN(date.getTime())) return isoStr;

    const pad = (n: number) => n.toString().padStart(2, "0");
    return (
      date.getFullYear() +
      "-" +
      pad(date.getMonth() + 1) +
      "-" +
      pad(date.getDate()) +
      " " +
      pad(date.getHours()) +
      ":" +
      pad(date.getMinutes()) +
      ":" +
      pad(date.getSeconds())
    );
  }

  function parseToUTC(localStr: string): string {
    if (localStr === DEFAULT_TIME_DISPLAY || localStr === DEFAULT_TIME) return DEFAULT_TIME;
    if (!localStr) return "";
    const date = new Date(localStr);
    // Remove milliseconds
    date.setMilliseconds(0);
    if (isNaN(date.getTime())) return localStr;
    const iso = date.toISOString();

    // Avoid redundant updates if logically equal
    const oldVal = arg.value;
    if (oldVal) {
      const oldDate = new Date(oldVal);
      if (!isNaN(oldDate.getTime()) && oldDate.getTime() === date.getTime()) {
        return oldVal;
      }
    }
    return iso;
  }

  // Local display value for datetime
  let displayValue = $state("");

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
  });

  // The actual error message to display
  const errorMessage = $derived(showError ? validationError : null);

  let debounceTimer: ReturnType<typeof setTimeout>;
  function onInput(event: Event) {
    // Hide error while typing
    showError = false;

    if (isDatetime) {
      arg.value = parseToUTC(displayValue);
    }

    // Clear the previous timer on each keystroke
    clearTimeout(debounceTimer);
    // Set a new timer to trigger the edit callback after a delay
    debounceTimer = setTimeout(() => {
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

  function onBlur() {
    // To prevent double-firing, clear any pending timer
    clearTimeout(debounceTimer);

    if (isDatetime) {
      arg.value = parseToUTC(displayValue);
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
    // Hide error on reset
    showError = false;
    // Trigger the provided reset callback
    arg.reset(handleReset);
  }
</script>

<div class={cn("group w-full", className)}>
  <div class="relative flex w-full items-center">
    <!-- Primary color bottom border -->
    <!-- On focus, gray bottom border and primary color rounded ring -->
    <Input
      type="text"
      class={cn(
        "bg-card dark:bg-card peer truncate border-0 p-1 px-2 shadow-none",
        isDatetime ? "pr-12 focus-visible:pr-12" : "focus-visible:pr-6",
        isDesc ? "py-0 text-muted-foreground" : "",
        "focus-visible:shadow-none",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-5",
        errorMessage && "ring-destructive ring-2 ring-offset-5",
      )}
      bind:value={inputValue.val}
      bind:ref={inputEl}
      oninput={onInput}
      onblur={onBlur}
    />

    <!-- Draw bottom border with peer -->
    <div
      class={cn(
        "peer-focus-visible:border-foreground/35 absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200",
        isDesc ? "group-hover:border-primary border-transparent" : "border-primary",
      )}
    ></div>

    <!-- Reset button is visible only on focus -->
    <div class="absolute right-1 flex items-center group-focus-within:right-0">
      {#if isDatetime}
        <span class="text-muted-foreground pointer-events-none text-xs select-none">{tzOffset}</span>
      {/if}
      <div
        class={cn(
          "flex items-center justify-center overflow-hidden transition-all duration-200",
          "w-0 group-focus-within:w-4",
        )}
      >
        <Reset {onReset} class="opacity-0 transition-opacity duration-200 group-focus-within:opacity-100" />
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

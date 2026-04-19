<script lang="ts">
  import { useArgValue, type InputProps } from "$lib/components/arg/utils.svelte";
  import { cn } from "$lib/utils";

  let {
    data = $bindable(),
    class: className,
    handleEdit,
    isRevtColor = false,
  }: InputProps & { isRevtColor?: boolean } = $props();
  // arg handler, use any as value type since it can be anything that evaluates to bool
  const arg = $derived(useArgValue<any>(data));

  // Determine boolean status: bool(value) = True is considered true
  const isEnabled = $derived(!!arg.value);

  let lastClickTime = 0;
  function toggle() {
    // 200ms anti-multi-click protection
    const now = Date.now();
    if (now - lastClickTime < 200) return;
    lastClickTime = now;

    // Value may not be True False, but we toggle and send true/false
    arg.value = !isEnabled;
    arg.submit(handleEdit);
  }
</script>

<button
  type="button"
  class={cn(
    "group relative flex h-7 w-full cursor-pointer items-center border-none bg-transparent p-1 px-2 text-left shadow-none ring-0 outline-none",
    "focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-5",
    className,
  )}
  onclick={toggle}
>
  <span
    class={cn(
      "truncate font-semibold transition-colors duration-200",
      isRevtColor
        ? isEnabled
          ? "text-white"
          : "text-muted-foreground"
        : isEnabled
          ? "text-primary"
          : "text-muted-foreground",
    )}
  >
    {arg.getLabel(isEnabled)}
  </span>

  <!-- Hover underline: only visible when hover -->
  <div
    class={cn(
      "absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200",
      isRevtColor
        ? isEnabled
          ? "group-hover:border-white"
          : "group-hover:border-muted-foreground"
        : isEnabled
          ? "group-hover:border-primary"
          : "group-hover:border-muted-foreground",
      "border-transparent",
    )}
  ></div>
</button>

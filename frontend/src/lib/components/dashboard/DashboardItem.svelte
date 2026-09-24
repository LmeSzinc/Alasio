<script lang="ts">
  import { untrack } from "svelte";
  import { cn } from "$lib/utils";
  import { type ArgData, getArgName } from "../arg/utils.svelte";
  import ColorDot from "./ColorDot.svelte";
  import PrettyValue from "./PrettyValue.svelte";
  import ReadableTime from "./ReadableTime.svelte";

  let {
    data,
    overrideFlash,
    class: className,
  }: {
    data: Record<string, ArgData>;
    /**
     * Pin the flash state of the item: `true` keeps it highlighted, `false`
     * keeps it plain, whatever its values do. Left undefined, the item flashes
     * on its own when one of its values is updated. The debug page pins items
     * to show both states without waiting for an update.
     */
    overrideFlash?: boolean;
    class?: string;
  } = $props();

  const info = $derived(data._info ?? {}) as ArgData;
  const time = $derived(data.Time?.value ?? "");

  // --- Flash on value change ---

  // How long the item stays highlighted after one of its values changed. An
  // update that arrives while the item still flashes restarts that second, so
  // the highlight ends one second after the last update instead of blinking
  // off in the middle of a burst of them (trading terminal).
  const FLASH_DURATION = 1000;

  // The values the item displays (see PrettyValue), as one comparable string:
  // every arg but the timestamp and the static `_info` metadata. The timestamp
  // is republished on every dashboard update, including the ones that carry no
  // new number, so it must never flash on its own.
  const valueKey = $derived(
    Object.entries(data)
      .filter(([argName]) => argName !== "_info" && argName !== "Time")
      .map(([argName, arg]) => `${argName}=${arg?.value}`)
      .join("|"),
  );

  let valueFlash = $state(false);
  // The value the item is mounted with is its starting point, not a change: an
  // item that appears with the first snapshot of the dashboard flashes only
  // once a later update changes one of its values.
  let lastValueKey = untrack(() => valueKey);
  // The record those values were read from (see the effect below).
  let lastData = untrack(() => data);

  $effect(() => {
    const record = data;
    const key = valueKey;
    // Switching to another config re-delivers the dashboard: the backend sends
    // the view of the new config and the websocket client puts it in place of
    // the view it held, so every record of the new view is a new object. An
    // update of the running config does the opposite, it patches one value of
    // the record in place (`set`) and leaves the record the very object it
    // was. A record that is not the object the item showed is therefore a view
    // delivered anew: its values were not updated under the item, they replace
    // what it displayed, which makes them a starting point like the values of
    // a mount, never a flash. A highlight that is still running belongs to the
    // values that just left the screen, so it ends with them.
    if (record !== lastData) {
      lastData = record;
      lastValueKey = key;
      valueFlash = false;
      return;
    }
    if (key === lastValueKey) {
      return;
    }
    lastValueKey = key;
    // A pinned item follows its caller, not its values.
    if (untrack(() => overrideFlash) !== undefined) {
      return;
    }
    valueFlash = true;
    const timeout = setTimeout(() => (valueFlash = false), FLASH_DURATION);
    // Runs before the next value change and on destroy: the previous deadline
    // is dropped here, which is what makes only the last update count.
    return () => clearTimeout(timeout);
  });

  const flashing = $derived(overrideFlash ?? valueFlash);
</script>

<!--
  The vertical padding is deliberately not symmetric: the value line keeps a
  24px line box for its 16px text, which leaves 7.5px of leading above the
  digits, while the 12px info line sits 2px above its 16px box bottom. With an
  even padding the highlight would show visibly more air over the value than
  under the name (measured 13.5px vs 8px); 4px over / 8px under balances the
  ink, and the item keeps its 54px height (the overview page budgets two whole
  items into its min-height).
-->
<div
  class={cn(
    "flex min-w-0 items-stretch gap-2.5 overflow-hidden rounded-md px-2.5 pt-1 pb-2 transition-colors duration-200",
    flashing && "bg-primary text-primary-foreground",
    className,
  )}
>
  <div class="mt-2">
    <!-- The dot joins the highlight while the item flashes, when it takes the
         same color as the text (`bg-(--dot-color)`, the color of the config,
         yields to it in ColorDot) -->
    <ColorDot
      color={data._info?.dashboard_color ?? "#777"}
      class={cn("h-2 w-2 rounded-full transition-colors duration-200", flashing && "bg-primary-foreground")}
    />
  </div>

  <div class="flex min-w-0 flex-1 flex-col gap-0.5">
    <!-- Pretty Value -->
    <PrettyValue {data} mutedClass={flashing ? "text-primary-foreground" : undefined} />

    <!-- Info -->
    <!-- Arg Name - Time -->
    <!--
      The name is the primary text, the time is the tail, and the line degrades
      in one direction only as the item gets narrower:
        "Oil - 3h ago" -> "Oil - 3..." -> "Oil" -> "O.."
      "O.. - ..." must never show up: an ellipsized name next to an ellipsized
      time spends the row on two pieces of text that both stop carrying
      information, while either of them alone still would.

      So the name does not yield at all (`max-w-full shrink-0`), it only gets
      clamped by the row, and the tail is the one that gives up room. Dropping
      the tail is all or nothing, never a bare separator left behind: the tail
      is worth showing from `basis-[2.5em]` (separator, one character and the
      ellipsis) up, and a flex line only takes the items whose hypothetical
      size fits, so a tail with less room than that wraps onto a second line,
      which `h-4` (the `text-xs` line-height) plus `overflow-hidden` clips
      away. Above that minimum the tail grows to the whole time and ellipsizes
      down to it, so the separator can only be seen together with the time.
    -->
    <div
      class={cn(
        "flex h-4 flex-wrap content-start items-center gap-0.5 overflow-hidden text-xs whitespace-nowrap transition-colors duration-200",
        flashing ? "text-primary-foreground" : "text-muted-foreground",
      )}
    >
      <span class="max-w-full shrink-0 truncate" title={getArgName(info)}>
        {getArgName(info)}
      </span>
      <span class="max-w-max min-w-0 grow basis-[2.5em] truncate">
        <span class="opacity-50">-</span>
        <ReadableTime {time} class="inline" />
      </span>
    </div>
  </div>
</div>

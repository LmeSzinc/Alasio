<script lang="ts">
  import { cn } from "$lib/utils";
  import { type ArgData, getArgName } from "../arg/utils.svelte";
  import ColorDot from "./ColorDot.svelte";
  import PrettyValue from "./PrettyValue.svelte";
  import ReadableTime from "./ReadableTime.svelte";

  let { data, class: className }: { data: Record<string, ArgData>; class?: string } = $props();

  const info = $derived(data._info ?? {}) as ArgData;
  const time = $derived(data.Time?.value ?? "");
</script>

<div class={cn("flex min-w-0 items-stretch gap-2.5 overflow-hidden pl-2", className)}>
  <div class="mt-2">
    <ColorDot color={data._info?.dashboard_color ?? "#777"} class="h-2 w-2 rounded-full" />
  </div>

  <div class="flex min-w-0 flex-1 flex-col gap-0.5">
    <!-- Pretty Value -->
    <PrettyValue {data} />

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
      class="text-muted-foreground flex h-4 flex-wrap content-start items-center gap-0.5 overflow-hidden text-xs whitespace-nowrap"
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

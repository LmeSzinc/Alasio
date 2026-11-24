<script lang="ts">
  import Copy from "@lucide/svelte/icons/copy";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { type InputProps, useArgValue } from "$lib/components/arg/utils.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { t } from "$lib/i18n";
  import { elementSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import {
    type PillBox,
    SECONDARY_SEPARATOR,
    cutPills,
    decodeRegex,
    duplicateIndexes,
    isRegex,
    isTwoLevel,
    splitOption,
    validItems,
  } from "./filterOrder";

  /** Max lines of pills shown in the settings page, the rest becomes "> ..." */
  const MAX_PILL_LINES = 5;

  /** The wrapping line of pills, shared by the visible list and the probe */
  const PILL_LINE = "flex flex-wrap content-start items-center gap-x-1 gap-y-0.5";

  let { data = $bindable(), class: className }: InputProps = $props();

  // The pills are read-only, the order is edited in the dialog of the row
  // action component (see FilterOrderAction.svelte / FilterOrderDialog.svelte).
  // `useArgValue` is used for the option_i18n label lookup only, the same as
  // dt=static does.
  const arg = $derived(useArgValue<any[]>(data));
  const dt = $derived(data.dt);
  const items = $derived(Array.isArray(data.value) ? data.value : []);
  const options = $derived(Array.isArray(data.option) ? data.option : []);
  // A two level filter-order writes every item of the value as "item-option",
  // see filterOrder.ts
  const optionDict = $derived(data.option_dict ?? {});
  const optionCommand = $derived(Array.isArray(data.option_command) ? data.option_command : []);
  const twoLevel = $derived(isTwoLevel(dt));
  // dt="filter-regex" has no "option": the candidates live in the level tables
  // and the item is decoded by "pattern"
  const regex = $derived(isRegex(dt));
  const levels = $derived(Array.isArray(data.levels) ? data.levels : []);
  const pattern = $derived(data.pattern ?? null);
  const valid = $derived(validItems(options, optionDict, dt, optionCommand));
  // A stored value may repeat an item: only the later occurrences are marked,
  // the first one is where the item belongs
  const duplicated = $derived(duplicateIndexes(items.map(String)));
  /**
   * The level values of every filter-regex item, decoded once per value
   *
   * The pill line and its hidden probe both render every pill, and each pill
   * reads its values twice (the invalid mark and the label), so decoding in the
   * label would run the pattern of the arg four times per item per render.
   */
  const decoded = $derived.by(() => {
    const map = new Map<string, ReturnType<typeof decodeRegex>>();
    if (!regex) return map;
    for (const item of items) {
      const text = String(item);
      map.set(text, optionCommand.includes(item) ? null : decodeRegex(pattern, text));
    }
    return map;
  });

  /** True for a command of a filter-regex, it carries no level at all */
  function isCommand(item: any): boolean {
    return regex && optionCommand.includes(item);
  }

  /**
   * The level values of a filter-regex item, null when the pattern can not read
   * it. A predicate outside the candidate tables is not an error, it only never
   * matches, so only an unreadable item is marked.
   */
  function regexValues(item: any) {
    return decoded.get(String(item)) ?? null;
  }

  /**
   * True for an item that is in the value but not in the legal items anymore,
   * e.g. an option that was removed from the yaml after the value was set, or
   * a secondary option that was removed from "option_dict" (the backend
   * repairs such a value on load, the demo shows the editor handling it)
   */
  function isInvalid(item: any): boolean {
    if (regex) return !isCommand(item) && regexValues(item) === null;
    if (twoLevel) return !valid.has(String(item));
    return options.length > 0 && !options.includes(item);
  }

  /**
   * Label of an item: its translated name, and after the level separator the
   * translated name of its secondary option, the same form the value is stored
   * in ("PlateT4-gun"). The parts are joined into one text instead of a second
   * element, so the pill reads as the stored item, not as two fields.
   */
  function labelOf(item: any): string {
    // A command carries no level, and it is shown as it is stored
    if (optionCommand.includes(item)) return String(item);
    // A filter-regex writes its levels "S9-E-315": the translated value of
    // every level, in the declared order
    if (regex) {
      const values = regexValues(item);
      if (!values) return String(item);
      return levels
        .map((level: { name: string }) => values[level.name] ?? "")
        .filter((value: string) => value !== "")
        .map((value: string) => arg.getLabel(value))
        .join(SECONDARY_SEPARATOR);
    }
    if (!twoLevel) return arg.getLabel(item);
    const [primary, secondary] = splitOption(String(item));
    if (secondary === null) return arg.getLabel(primary);
    return `${arg.getLabel(primary)}${SECONDARY_SEPARATOR}${arg.getLabel(secondary)}`;
  }

  // --- Cut of the pill lines ---
  // The list shows at most MAX_PILL_LINES lines and ends with "> ..." when
  // something is left. Which pills fit is measured on a hidden probe of the
  // whole order (see the markup), so the cut is computed from one render of
  // every pill instead of rendering candidate cuts one after another: the
  // visible list is only ever painted in its final shape, and a resize costs
  // one measurement, not a loop of ticks.

  /** The hidden probe of the whole order, the measuring source of `measure()` */
  let probe: HTMLElement | null = $state(null);
  /** Size of the probe, kept up to date by `elementSize` */
  let probeSize = $state({ width: 0, height: 0 });
  /** Pills to show, null before the first measurement (every pill is shown) */
  let shown: number | null = $state(null);
  const visible = $derived(shown === null ? items : items.slice(0, shown));
  const truncated = $derived(shown !== null && shown < items.length);

  /** Everything a pill renders from: a change re-measures (the labels set the widths) */
  const layoutKey = $derived(JSON.stringify([items, options, optionDict, data.option_i18n ?? {}]));

  /**
   * Cut the order to the first MAX_PILL_LINES lines. Only reads the geometry of
   * the probe, so it can run at any time, without a render of its own.
   */
  function measure() {
    const el = probe;
    // clientWidth is 0 while an ancestor is hidden: the row is not laid out
    // yet, the probe resizes (and re-measures) once it shows up
    if (!el || el.clientWidth === 0) return;
    const units = [...el.children] as HTMLElement[];
    // The last child of the probe is the marker, it measures the marker width
    const marker = units.pop();
    if (!marker) return;
    const boxes: PillBox[] = units.map((unit) => {
      const rect = unit.getBoundingClientRect();
      return { top: unit.offsetTop, right: rect.right };
    });
    const edge = el.getBoundingClientRect().right;
    const gap = parseFloat(getComputedStyle(el).columnGap) || 0;
    shown = cutPills(boxes, marker.getBoundingClientRect().width, gap, edge, MAX_PILL_LINES);
  }

  $effect(() => {
    const el = probe;
    // Re-measure on a content change and on a probe size change (a narrower
    // line wraps the pills differently). The first measurement runs here, right
    // after the row is mounted, before anything is painted
    void layoutKey;
    void probeSize.width;
    void probeSize.height;
    if (!el) return;
    measure();
  });
</script>

{#snippet pill(item: any, index: number)}
  <!-- Each unit binds the ">" to the pill after it, so a wrapped line can only
       start with ">", a line never ends with one -->
  <span class="inline-flex items-center gap-x-1">
    {#if index > 0}
      <span class="text-xs text-muted-foreground">&gt;</span>
    {/if}
    <Badge variant="secondary" class="max-w-full rounded-full">
      {#if isInvalid(item)}
        <!-- The settings page only marks the item, the tooltip of the invalid
             item lives in the editor (see FilterOrderItem.svelte) -->
        <span role="img" class="inline-flex shrink-0" aria-label={t.Input.FilterOrderInvalid()}>
          <TriangleAlert class="size-3 shrink-0 text-destructive" />
        </span>
      {/if}
      {#if duplicated.has(index)}
        <!-- A different shape from the invalid mark: the item itself is legal,
             it is its position that can not be read -->
        <span role="img" class="inline-flex shrink-0" aria-label={t.Input.FilterOrderDuplicate()}>
          <Copy class="size-3 shrink-0 text-amber-600 dark:text-amber-400" />
        </span>
      {/if}
      <!-- A long item truncates inside the pill instead of widening it past
           the card, the pill itself is capped to the line width -->
      <span class="min-w-0 truncate">{labelOf(item)}</span>
    </Badge>
  </span>
{/snippet}

<div class={cn("relative", className)}>
  <!-- The visible list: the pills up to the cut, then the marker -->
  <div data-slot="filter-order-pills" class={PILL_LINE}>
    {#if items.length === 0}
      <span class="text-xs text-muted-foreground">{t.Input.FilterOrderEmpty()}</span>
    {:else}
      <!-- The key carries the index: a stored value may repeat an item, and the
           item alone would then be a duplicate key (the pill line would not
           render at all, hiding the very value the mark is about) -->
      {#each visible as item, index (`${index}\u0000${item}`)}
        {@render pill(item, index)}
      {/each}
      {#if truncated}
        <span class="text-xs text-muted-foreground">&gt; ...</span>
      {/if}
    {/if}
  </div>

  <!-- Hidden probe of the whole order, the measuring source of the cut: out of
       flow, never painted and unreachable for tab and screen readers. Its
       trailing marker measures the width of the marker -->
  {#if items.length > 0}
    <div
      bind:this={probe}
      use:elementSize={probeSize}
      data-slot="filter-order-probe"
      aria-hidden="true"
      inert
      class={cn(PILL_LINE, "pointer-events-none invisible absolute inset-x-0 top-0")}
    >
      {#each items as item, index (`${index}\u0000${item}`)}
        {@render pill(item, index)}
      {/each}
      <span class="text-xs text-muted-foreground">&gt; ...</span>
    </div>
  {/if}
</div>

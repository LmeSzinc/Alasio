<script lang="ts">
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { type InputProps, useArgValue } from "$lib/components/arg/utils.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { t } from "$lib/i18n";
  import { elementSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import { type PillBox, cutPills } from "./filterOrder";

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
  const items = $derived(Array.isArray(data.value) ? data.value : []);
  const options = $derived(Array.isArray(data.option) ? data.option : []);

  /**
   * True for an item that is in the value but not in "option" anymore, e.g. an
   * option that was removed from the yaml after the value was set (the backend
   * repairs such a value on load, the demo shows the editor handling it)
   */
  function isInvalid(item: any): boolean {
    return options.length > 0 && !options.includes(item);
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
  const layoutKey = $derived(JSON.stringify([items, options, data.option_i18n ?? {}]));

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
      <span class="text-muted-foreground text-xs">&gt;</span>
    {/if}
    <Badge variant="secondary" class="rounded-full">
      {#if isInvalid(item)}
        <Tooltip.Root>
          <Tooltip.Trigger>
            <span role="img" class="inline-flex" aria-label={t.Input.FilterOrderInvalid()}>
              <TriangleAlert class="text-destructive size-3 shrink-0" />
            </span>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>{t.Input.FilterOrderInvalid()}</p>
          </Tooltip.Content>
        </Tooltip.Root>
      {/if}
      {arg.getLabel(item)}
    </Badge>
  </span>
{/snippet}

<Tooltip.Provider>
  <div class={cn("relative", className)}>
    <!-- The visible list: the pills up to the cut, then the marker -->
    <div data-slot="filter-order-pills" class={PILL_LINE}>
      {#if items.length === 0}
        <span class="text-muted-foreground text-xs">{t.Input.FilterOrderEmpty()}</span>
      {:else}
        {#each visible as item, index (item)}
          {@render pill(item, index)}
        {/each}
        {#if truncated}
          <span class="text-muted-foreground text-xs">&gt; ...</span>
        {/if}
      {/if}
    </div>

    <!-- Hidden probe of the whole order, the measuring source of the cut: out
         of flow, never painted and unreachable for tab and screen readers. Its
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
        {#each items as item, index (item)}
          {@render pill(item, index)}
        {/each}
        <span class="text-muted-foreground text-xs">&gt; ...</span>
      </div>
    {/if}
  </div>
</Tooltip.Provider>

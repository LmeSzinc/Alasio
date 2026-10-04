<script lang="ts">
  import { tick } from "svelte";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { type InputProps, useArgValue } from "$lib/components/arg/utils.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";

  /** Max lines of pills shown in the settings page, the rest becomes "> ..." */
  const MAX_PILL_LINES = 5;

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

  let pills: HTMLElement | null = $state(null);
  // Number of items to show, 0 = not measured yet (render every item)
  let shown = $state(0);
  const visible = $derived(shown > 0 ? items.slice(0, shown) : items);
  const truncated = $derived(shown > 0 && shown < items.length);

  /** Width of the last measurement, a resize only re-measures when it changes */
  let measuredWidth = -1;

  /**
   * Count the items that fit into the first MAX_PILL_LINES lines. The count is
   * taken from a full render: the items behind the cut would be hidden and
   * could not be measured anymore. Both state writes happen in one task, so no
   * frame is painted with the full list.
   */
  async function measure() {
    const el = pills;
    if (!el) return;
    if (shown !== 0) {
      shown = 0;
      await tick();
    }
    const lines = new Set<number>();
    let count = 0;
    for (const unit of [...el.children] as HTMLElement[]) {
      const top = unit.offsetTop;
      if (!lines.has(top)) {
        if (lines.size >= MAX_PILL_LINES) break;
        lines.add(top);
      }
      count++;
    }
    // The trailing "> ..." must stay inside the lines above: drop items from
    // the cut line until the marker fits there (usually one item is enough)
    while (count > 0 && count < items.length) {
      shown = count;
      await tick();
      const marker = el.lastElementChild as HTMLElement | null;
      const last = marker?.previousElementSibling as HTMLElement | null;
      if (!marker || !last) break;
      // A wrapped marker is a whole line below the last item; within one line
      // the vertical centering keeps the tops within a few pixels
      if (marker.offsetTop - last.offsetTop < 10) break;
      count--;
    }
    shown = count;
  }

  $effect(() => {
    const el = pills;
    if (!el) return;
    void items.length; // re-measure when the value changes
    const observer = new ResizeObserver((entries) => {
      // The measurement changes the height of the pill row, only a width
      // change (a wider / narrower card) needs another measurement
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      if (width === measuredWidth) return;
      measuredWidth = width;
      void measure();
    });
    observer.observe(el);
    return () => observer.disconnect();
  });
</script>

<!-- Each unit binds the ">" to the pill after it, so a wrapped line can only
     start with ">", a line never ends with one -->
<Tooltip.Provider>
  <div
    bind:this={pills}
    data-slot="filter-order-pills"
    class={cn("flex flex-wrap content-start items-center gap-x-1 gap-y-0.5", className)}
  >
    {#if items.length === 0}
      <span class="text-muted-foreground text-xs">{t.Input.FilterOrderEmpty()}</span>
    {:else}
      {#each visible as item, index (item)}
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
      {/each}
      {#if truncated}
        <span class="text-muted-foreground text-xs">&gt; ...</span>
      {/if}
    {/if}
  </div>
</Tooltip.Provider>

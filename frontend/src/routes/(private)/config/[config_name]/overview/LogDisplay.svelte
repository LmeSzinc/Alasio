<script lang="ts">
  import ArrowDownToLine from "@lucide/svelte/icons/arrow-down-to-line";
  import Button from "$lib/components/ui/button/button.svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import LogData from "./LogData.svelte";
  import type { LogDataProps } from "./types";

  type Props = {
    data?: LogDataProps[];
    class?: string;
  };
  let { data: logData, class: className }: Props = $props();
  let logCard: HTMLDivElement | null = $state(null);
  let logContainer: HTMLDivElement | null = $state(null);
  let lineProbe: HTMLPreElement | null = $state(null);
  let isInitial = $state(true);

  /** Gap kept between the log and the card's top and bottom edges, in px */
  const EDGE_GAP = 10;

  /** Whether the card was fitted to whole lines at least once */
  let fitted = false;

  /** Whether the log holds no entries, so the card shows the hint */
  const isEmpty = $derived(!logData || logData.length === 0);

  /**
   * Scroll to the end of the log.
   *
   * An offset past the end is clamped to the end, so this reaches the newest
   * entry without reading the layout: a batch of a thousand entries would
   * otherwise force the layout early, in the middle of the update.
   *
   * Args:
   *     view (HTMLDivElement): The scroll area viewport
   */
  function scrollToEnd(view: HTMLDivElement) {
    view.scrollTop = Number.MAX_SAFE_INTEGER;
    view.scrollLeft = 0;
  }

  /**
   * Fit the scrolling area to a whole number of log lines.
   *
   * The log is read from the bottom, and every entry is a whole number of line
   * boxes tall with no padding of its own (see LogData.svelte), so a viewport of
   * a whole number of lines puts both of its edges on a line boundary: the
   * bottom edge ends the last line and the top edge starts one, instead of
   * cutting the glyphs off a line.
   *
   * The card is rarely a whole number of lines tall, so the rest of the last
   * line is split between the two edge gaps. The gaps stay even at any card
   * height, and the leftover pixels are part of the gap rather than content, so
   * the newest entry stays the last thing the log can reach.
   *
   * The empty hint is not part of the scrolling area (see the markup), so it
   * never enters this geometry: with no entries to align there is nothing to
   * fit, and the gaps it leaves are blank space.
   *
   * Called on a card resize and on a change of the line height, not on a new
   * entry: a new entry changes the content, never the geometry.
   */
  function fitLines() {
    const card = logCard;
    const probe = lineProbe;
    if (!card || !probe) {
      return;
    }
    const pitch = probe.getBoundingClientRect().height;
    if (!(pitch > 0)) {
      return;
    }
    // The card's own height does not depend on the gaps it carries, so it is the
    // one stable reference for the fit
    const bounds = getComputedStyle(card);
    const borders = (parseFloat(bounds.borderTopWidth) || 0) + (parseFloat(bounds.borderBottomWidth) || 0);
    const room = card.getBoundingClientRect().height - borders - 2 * EDGE_GAP;
    if (!(room > 0)) {
      return;
    }
    const rest = room >= pitch ? room % pitch : 0;
    const gap = `${EDGE_GAP + rest / 2}px`;
    if (card.style.paddingTop !== gap) {
      card.style.paddingTop = gap;
    }
    if (card.style.paddingBottom !== gap) {
      card.style.paddingBottom = gap;
    }
    fitted = true;
  }

  // auto scroll on data changes
  let scrollRAF: number | null = null;
  function scrollToBottom() {
    scrollRAF = null;
    if (!logContainer) {
      return;
    }
    if (!fitted) {
      // The first batch can arrive before the resize observer reports the card
      fitLines();
    }
    scrollToEnd(logContainer);
    if (isInitial && logData && logData.length > 0) {
      isInitial = false;
    }
  }
  $effect(() => {
    // tracking the length of logs
    const length = logData?.length ?? 0;
    if (logContainer && scrollRAF === null && length > 0) {
      scrollRAF = requestAnimationFrame(scrollToBottom);
    }
  });
  // The layout resizes the card (a window resize, a card shown or hidden next to
  // it) and a font change resizes the line, and both move the geometry. Watching
  // the card's border box and the line probe covers exactly those two cases: the
  // gaps live in the card's padding, so fitting them does not resize the border
  // box and the observer settles instead of looping.
  $effect(() => {
    const card = logCard;
    const probe = lineProbe;
    const view = logContainer;
    if (!card || !probe || !view) {
      return;
    }
    const observer = new ResizeObserver(() => {
      fitLines();
      // A narrower card re-wraps the messages, so the pixel offset of the old
      // position means nothing any more; the log follows the newest entry, which
      // is also where a resize leaves it
      scrollToEnd(view);
    });
    observer.observe(card, { box: "border-box" });
    observer.observe(probe);
    return () => observer.disconnect();
  });
</script>

<div
  bind:this={logCard}
  data-slot="log-card"
  class={cn("neushadow group @container relative h-full max-h-screen w-full rounded-lg bg-card px-2.5", className)}
>
  <!--
    One row of the log typography, out of the flow and invisible: its height is
    one row of the log, measured on the spot. It has to carry the same classes as
    an entry (see LogData.svelte) or the two disagree by a fraction of a row. The
    row is measured rather than computed: a computed line-height is reported in
    whole pixels while the box the layout produces can be a fraction less, and a
    fraction per row adds up to a cut line over a thousand entries. The probe
    does not depend on the entries either -- the log may be empty, and its first
    message may wrap.
  -->
  <pre
    bind:this={lineProbe}
    data-slot="log-line-probe"
    aria-hidden="true"
    class="pointer-events-none invisible absolute top-0 left-0 font-mono text-xs leading-[1.125rem]">|</pre>
  <ScrollArea class="h-full w-full" orientation="both" bind:viewportRef={logContainer}>
    <!--
      No vertical padding: an entry has to stay a whole number of line boxes
      tall for the log to land on the line grid (see fitLines).
    -->
    <div data-slot="log-content" class={cn("flex flex-col", isInitial && logData && logData.length > 0 && "invisible")}>
      {#each logData ?? [] as log (log)}
        <LogData {...log} />
      {/each}
    </div>
  </ScrollArea>
  <!--
    The empty hint is an overlay of the card, not a row of the log: it carries
    no line boxes, so it stays out of the geometry fitLines aligns and out of
    the scroll content. One spacing step below the card's top edge (top-4, the
    offset the scroll-to-bottom button sits on) puts it where the first entry's
    text would begin, a little under the edge gap the fitted log uses, so it
    does not hug the card edge while the card resizes around it.
  -->
  {#if isEmpty}
    <div data-slot="log-empty" class="pointer-events-none absolute inset-x-0 top-4 flex justify-center">
      <span class="text-sm text-muted-foreground italic">{t.Overview.LogEmpty()}</span>
    </div>
  {/if}
  <Button
    onclick={scrollToBottom}
    class="absolute top-4 right-4 z-10 bg-card opacity-0 transition-opacity group-hover:opacity-100"
    size="icon-sm"
    variant="outline"
  >
    <ArrowDownToLine />
  </Button>
</div>

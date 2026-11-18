<script lang="ts">
  import { untrack } from "svelte";
  import { cn } from "$lib/utils";

  /**
   * One run of text that steps down through a list of sizes until it fits the
   * box it is given.
   *
   * The sizes are tried largest first: a hidden copy of the text is measured at
   * each size (`whitespace-nowrap`, so the measurement is the whole line), and
   * the first size that fits is the one rendered. When even the smallest does
   * not fit, the smallest is rendered with `line-clamp-2` instead: the text then
   * wraps into the narrow box rather than being cut, and only a text that still
   * needs a third line ends in an ellipsis. The box never grows, whatever the
   * text and the language.
   *
   * The measurement runs on layout changes as well as on new text: the box can
   * be resized (the sheet hands its column another width) and the web font can
   * arrive after the first paint, which makes every line wider than the fallback
   * font it was measured with. A ResizeObserver and the font loading promise
   * both re-run it, so no text is left wider than the box measured for it.
   */
  type Size = {
    /** Class of the hidden measuring copy; it must repeat the display size */
    probe: string;
    /** Class of the rendered text at this size */
    display: string;
  };

  type Props = {
    text: string;
    /** Candidate sizes, largest first */
    sizes: Size[];
    /** Class of the box, whose width is what the text has to fit */
    class?: string;
    /** Extra class of the rendered text, e.g. its colour or weight */
    textClass?: string;
  };
  let { text, sizes, class: className, textClass }: Props = $props();

  let boxRef = $state<HTMLElement | null>(null);
  let probeRefs = $state<(HTMLElement | null)[]>([]);
  // Bumped when the box is resized or a font finished loading
  let layoutVersion = $state(0);
  // Index of the first size that fits, -1 when none does
  let fitIndex = $state(-1);

  $effect(() => {
    const box = boxRef;
    if (!box) return;
    const bump = () => untrack(() => (layoutVersion += 1));
    // A browser without ResizeObserver simply keeps the first measurement; the
    // text is then as good as the box width it was measured at
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(bump);
    observer?.observe(box);
    // The first paint may use a fallback font: measure again once the real one
    // is in use
    let alive = true;
    document.fonts?.ready.then(() => {
      if (alive) bump();
    });
    return () => {
      alive = false;
      observer?.disconnect();
    };
  });

  $effect(() => {
    // Everything the choice is based on; the box width is read below
    text;
    sizes;
    layoutVersion;
    const box = boxRef;
    const probes = probeRefs;
    if (!box) return;
    if (probes.length !== sizes.length || probes.some((probe) => probe === null)) return;
    const width = box.clientWidth;
    // Before layout (or in a display:none subtree) there is nothing to fit into
    if (width === 0) return;

    fitIndex = probes.findIndex((probe) => probe!.scrollWidth <= width);
  });

  const shownIndex = $derived(fitIndex === -1 ? Math.max(sizes.length - 1, 0) : fitIndex);
  const wraps = $derived(fitIndex === -1);
</script>

<div bind:this={boxRef} class={cn("relative overflow-hidden", className)}>
  {#each sizes as size, i (i)}
    <!-- The probe carries the same text class as the rendered span: a weight or
         a font that differs would measure a narrower line than the one drawn
         and the text would be chosen for a size it does not fit -->
    <span
      bind:this={probeRefs[i]}
      class={cn("invisible absolute top-0 left-0 whitespace-nowrap", size.probe, textClass)}
      aria-hidden="true">{text}</span
    >
  {/each}
  <span class={cn(textClass, sizes[shownIndex]?.display, "wrap-break-word", wraps && "line-clamp-2")}>
    {text}
  </span>
</div>

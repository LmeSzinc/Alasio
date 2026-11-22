<script lang="ts">
  import { tick, untrack } from "svelte";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import X from "@lucide/svelte/icons/x";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Dialog from "$lib/components/ui/dialog/index.js";
  import Image from "$lib/components/ui/image/Image.svelte";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils.js";
  import { dragRegionState } from "$lib/window/dragRegion.svelte";
  import FilepathTitle from "./FilepathTitle.svelte";
  import { tokenizePython } from "./python";
  import { type SourceSegment, buildSegments, normalizeImages } from "./segments";
  import { type GoldenTestFileData, imageUrl } from "./types";

  /**
   * The source of the selected test file, with a screenshot beside each test.
   *
   * The lines and the images live in one scroll container: every image starts a
   * row at the line of the first decorator of its test, so scrolling the code
   * scrolls the screenshots with it. A segment is a line range of the
   * highlighted source, never a copy: the line numbers are the numbers of the
   * file.
   *
   * The two columns never mix. The code column can not be narrower than its
   * longest line (a `min-content` grid track), so a long line widens the row
   * instead of running under the screenshots, and the scroll container below
   * scrolls the row sideways. The image column sticks to the right edge while
   * that happens, so the screenshot of the segment being read stays in view.
   */
  type Props = {
    data: GoldenTestFileData;
    showImages: boolean;
    /**
     * Build the URL of a screenshot from the path `@image` wrote.
     *
     * The review page reads the collection of the browsed mod through the
     * backend; a page with mock data (the demo of the dev tools) passes one
     * bundled image instead. Defaults to the image route of the mod.
     */
    resolveImage?: (path: string) => string;
  };

  let { data, showImages, resolveImage }: Props = $props();

  const source = $derived(data.source ?? "");
  const lines = $derived(tokenizePython(source));
  const images = $derived(normalizeImages(data.images));
  const segments = $derived(buildSegments(lines.length, images));
  const mod = $derived(data.mod_name ?? "");
  const errors = $derived(data.errors ?? []);

  /**
   * Height of the band the desktop client makes draggable over the window top.
   *
   * The band is declared by the layout and narrowed by the page, so it is read
   * from the folded region instead of being assumed: a page that reserves more
   * of the header moves the overlay further down with it. A plain browser has
   * no host and therefore no band, which leaves the overlay centred in the
   * window as before.
   */
  const bandHeight = $derived.by(() => {
    const height = dragRegionState.current?.height;
    if (height === undefined) return "0px";
    return typeof height === "number" ? `${height}px` : height;
  });
  /**
   * Centre of the area below the band: the overlay is centred there rather than
   * in the window, so it never covers the band.
   */
  const bandCentre = $derived(`calc(50% + ${bandHeight} / 2)`);
  /** The overlay clears the band and keeps 12px of the area on either side */
  const bandMaxHeight = $derived(`calc(100% - ${bandHeight} - 24px)`);

  /** URL of the screenshot the overlay shows */
  let zoomSrc = $state("");
  let zoomName = $state("");
  let zoomOpen = $state(false);
  let actualSize = $state(false);

  let scrollEl: HTMLDivElement | undefined = $state();
  /** The file the page last scrolled for, an update of it must not move the page */
  let scrolledFor = "";

  $effect(() => {
    const file = data.file ?? "";
    if (file !== scrolledFor) {
      scrolledFor = file;
      zoomOpen = false;
    }
  });

  // Bring the first screenshot into view when a file is opened, after the
  // segments are laid out
  $effect(() => {
    const file = data.file ?? "";
    const first = segments.find((segment) => segment.image !== null);
    if (!scrollEl || file === "" || segments.length === 0) return;
    if (untrack(() => scrolledFor) !== file) return;
    const start = first?.start ?? 1;
    void tick().then(() => {
      scrollEl?.querySelector(`[data-start-line="${start}"]`)?.scrollIntoView({ block: "start" });
    });
  });

  function lineRange(segment: SourceSegment): number[] {
    return Array.from({ length: segment.end - segment.start + 1 }, (_, i) => segment.start + i);
  }

  function openZoom(src: string, name: string) {
    actualSize = false;
    zoomSrc = src;
    zoomName = name;
    zoomOpen = true;
  }

  /** The classes of the token colors of python.ts */
  const SPAN_CLASS: Record<string, string> = {
    keyword: "text-violet-600 dark:text-violet-400",
    constant: "text-amber-600 dark:text-amber-400",
    builtin: "text-sky-600 dark:text-sky-400",
    string: "text-emerald-600 dark:text-emerald-400",
    comment: "text-muted-foreground italic",
    number: "text-orange-600 dark:text-orange-400",
    decorator: "text-pink-600 dark:text-pink-400",
    defname: "text-blue-600 dark:text-blue-400",
    classname: "text-yellow-600 dark:text-yellow-500",
  };
</script>

<div class="flex h-full min-h-0 w-full flex-col">
  {#if errors.length > 0}
    <div class="shrink-0 border-b border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      <div class="flex items-center gap-1.5 font-medium">
        <TriangleAlert class="size-3.5 shrink-0" />
        {t.GoldenTest.SyntaxError()}
      </div>
      {#each errors as error (error)}
        <div class="mt-1 font-mono whitespace-pre-wrap">{error}</div>
      {/each}
    </div>
  {/if}

  <div class="min-h-0 flex-1 overflow-auto" bind:this={scrollEl}>
    {#if segments.length === 0}
      <div class="p-6 text-sm text-muted-foreground">
        {data.file ? t.GoldenTest.NoSource() : t.GoldenTest.NoFile()}
      </div>
    {:else}
      <!-- One grid for the whole file, not one per segment: the two columns
           share their widths across every row, so the divider between the code
           and the screenshots is the same line all the way down. A segment
           contributes its two cells in order, the row height is the taller of
           them and both cells stretch to it, which puts their bottom borders
           on one line too. -->
      <div
        class="grid items-stretch"
        style:grid-template-columns={showImages
          ? "minmax(min-content, 2fr) minmax(220px, 1fr)"
          : "minmax(min-content, 1fr)"}
      >
        {#each segments as segment (segment.start)}
          <!-- min-content keeps the column as wide as its longest line, it can
               never shrink over the screenshots. The two tracks are 2fr / 1fr:
               the code gets two thirds of the width, the screenshots one. -->
          <div class="min-w-0 border-b border-border/60 py-1" data-start-line={segment.start}>
            {#each lineRange(segment) as no (no)}
              <div class="flex font-mono text-xs leading-5">
                <span class="w-11 shrink-0 pr-2 text-right text-muted-foreground/70 tabular-nums select-none">
                  {no}
                </span>
                <span class="whitespace-pre">
                  {#each lines[no - 1] ?? [] as span, index (index)}<span class={SPAN_CLASS[span.cls] ?? ""}>
                      {span.text}
                    </span>{/each}
                </span>
              </div>
            {/each}
          </div>

          {#if showImages}
            <!-- Sticky to the right edge: the row may be wider than the viewport
                 (a long line), and the screenshot stays in view while the code
                 is read sideways. The solid background is what keeps the code
                 out of this column while it scrolls underneath -->
            <div class="sticky right-0 z-10 min-w-0 border-b border-l border-border/60 bg-background p-2">
              {#if segment.image !== null}
                {@const image = segment.image}
                {#if image.image === ""}
                  <div class="flex items-start gap-1.5 text-xs text-muted-foreground italic">
                    <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
                    <span>{image.error || t.GoldenTest.NoImage()}</span>
                  </div>
                {:else}
                  {@const src = resolveImage ? resolveImage(image.image) : imageUrl(mod, image.image)}
                  <button
                    type="button"
                    class="block w-full cursor-zoom-in"
                    title={image.image}
                    onclick={() => openZoom(src, image.image)}
                  >
                    <!-- The screenshots of a file are all above the fold of one
                         long page: the component loads one when it comes near
                         the viewport, not every one of them on open -->
                    <Image
                      class="h-auto min-h-9 w-full"
                      imageClass="max-w-full rounded border border-border/60"
                      {src}
                      alt={image.func_name}
                      rootMargin="300px"
                      loadingDelay={200}
                    >
                      {#snippet error()}
                        <div class="flex items-start gap-1.5 text-xs text-muted-foreground italic">
                          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
                          <span>{t.GoldenTest.MissingImage()}</span>
                        </div>
                      {/snippet}
                    </Image>
                  </button>
                  <div class="mt-1 truncate font-mono text-xs text-muted-foreground" title={image.image}>
                    {image.image}
                  </div>
                {/if}
              {/if}
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  </div>
</div>

<Dialog.Root bind:open={zoomOpen}>
  <!-- The close button of the dialog content is the absolutely positioned one of
       the component, which sits on the corner of the dialog rather than on the
       row of the toolbar: the overlay draws its own close button at the end of
       that row instead, and keeps the docked one off.

       The overlay is centred in the area below the drag band of the desktop
       client, not in the window: a band over the toolbar would swallow its
       clicks, and the band is declared per page, so its height comes from
       `dragRegionState` instead of a fixed header size. -->
  <Dialog.Content
    class="flex h-[90vh] w-[95vw] max-w-[95vw] flex-col gap-2 p-3 sm:max-w-[95vw]"
    aria-label={zoomName}
    showCloseButton={false}
    style="top: {bandCentre}; max-height: {bandMaxHeight};"
  >
    <!-- One row for every control of the overlay: the title takes what is left,
         the zoom buttons and the close button line up at the right end. -->
    <div class="flex shrink-0 items-center gap-2">
      <FilepathTitle path={zoomName} />
      <!-- The zoom mode is the Button variant of its state: the mode in use is
           the solid default button, the other one is outlined. The outlined
           button paints `bg-background`, a grey next to the white popover of
           the dialog, so its fill is cleared to keep it hollow. -->
      <Button
        variant={actualSize ? "outline" : "default"}
        size="sm"
        class={actualSize ? "bg-transparent" : undefined}
        onclick={() => (actualSize = false)}
      >
        {t.GoldenTest.Fit()}
      </Button>
      <Button
        variant={actualSize ? "default" : "outline"}
        size="sm"
        class={actualSize ? undefined : "bg-transparent"}
        onclick={() => (actualSize = true)}
      >
        {t.GoldenTest.ActualSize()}
      </Button>
      <Dialog.Close>
        {#snippet child({ props })}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t.GoldenTest.Close()}
            title={t.GoldenTest.Close()}
            {...props}
          >
            <X class="size-4" />
          </Button>
        {/snippet}
      </Dialog.Close>
    </div>
    <div class="flex min-h-0 flex-1 cursor-zoom-out overflow-auto rounded bg-muted/40">
      <button
        type="button"
        class={cn("flex h-full w-full p-2", actualSize ? "items-start justify-start" : "items-center justify-center")}
        aria-label={t.GoldenTest.Close()}
        onclick={() => (zoomOpen = false)}
      >
        <Image
          class="h-full w-full"
          imageClass={actualSize ? "h-auto w-auto max-h-none max-w-none" : ""}
          src={zoomSrc}
          alt={zoomName}
          rootMargin={null}
          style={actualSize ? "image-rendering: pixelated" : undefined}
        />
      </button>
    </div>
  </Dialog.Content>
</Dialog.Root>

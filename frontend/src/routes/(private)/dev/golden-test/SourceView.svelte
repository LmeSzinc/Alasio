<script lang="ts">
  import { tick, untrack } from "svelte";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Dialog from "$lib/components/ui/dialog/index.js";
  import Image from "$lib/components/ui/image/Image.svelte";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils.js";
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
  };

  let { data, showImages }: Props = $props();

  const source = $derived(data.source ?? "");
  const lines = $derived(tokenizePython(source));
  const images = $derived(normalizeImages(data.images));
  const segments = $derived(buildSegments(lines.length, images));
  const mod = $derived(data.mod_name ?? "");
  const errors = $derived(data.errors ?? []);

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
    <div class="border-destructive/40 bg-destructive/10 text-destructive shrink-0 border-b px-3 py-2 text-xs">
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
      <div class="text-muted-foreground p-6 text-sm">
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
          <div class="border-border/60 min-w-0 border-b py-1" data-start-line={segment.start}>
            {#each lineRange(segment) as no (no)}
              <div class="flex font-mono text-xs leading-5">
                <span class="text-muted-foreground/70 w-11 shrink-0 pr-2 text-right tabular-nums select-none">
                  {no}
                </span>
                <span class="whitespace-pre"
                  >{#each lines[no - 1] ?? [] as span, index (index)}<span class={SPAN_CLASS[span.cls] ?? ""}
                      >{span.text}</span
                    >{/each}</span
                >
              </div>
            {/each}
          </div>

          {#if showImages}
            <!-- Sticky to the right edge: the row may be wider than the viewport
                 (a long line), and the screenshot stays in view while the code
                 is read sideways. The solid background is what keeps the code
                 out of this column while it scrolls underneath -->
            <div class="border-border/60 bg-background sticky right-0 z-10 min-w-0 border-b border-l p-2">
              {#if segment.image !== null}
                {@const image = segment.image}
                {#if image.image === ""}
                  <div class="text-muted-foreground flex items-start gap-1.5 text-xs italic">
                    <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
                    <span>{image.error || t.GoldenTest.NoImage()}</span>
                  </div>
                {:else}
                  {@const src = imageUrl(mod, image.image)}
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
                        <div class="text-muted-foreground flex items-start gap-1.5 text-xs italic">
                          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
                          <span>{t.GoldenTest.MissingImage()}</span>
                        </div>
                      {/snippet}
                    </Image>
                  </button>
                  <div class="text-muted-foreground mt-1 truncate font-mono text-xs" title={image.image}>
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
  <Dialog.Content class="flex h-[90vh] w-[95vw] max-w-[95vw] flex-col gap-2 p-3 sm:max-w-[95vw]" aria-label={zoomName}>
    <div class="flex shrink-0 items-center justify-between gap-2 pr-8">
      <span class="text-muted-foreground truncate font-mono text-xs" title={zoomName}>{zoomName}</span>
      <div class="flex shrink-0 items-center gap-1">
        <Button variant={actualSize ? "outline" : "secondary"} size="sm" onclick={() => (actualSize = false)}>
          {t.GoldenTest.Fit()}
        </Button>
        <Button variant={actualSize ? "secondary" : "outline"} size="sm" onclick={() => (actualSize = true)}>
          {t.GoldenTest.ActualSize()}
        </Button>
      </div>
    </div>
    <div class="bg-muted/40 flex min-h-0 flex-1 cursor-zoom-out overflow-auto rounded">
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

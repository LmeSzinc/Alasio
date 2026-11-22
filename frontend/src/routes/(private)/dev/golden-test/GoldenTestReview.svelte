<script lang="ts">
  import Images from "@lucide/svelte/icons/images";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Toggle } from "$lib/components/ui/toggle/index.js";
  import { t } from "$lib/i18n";
  import SourceView from "./SourceView.svelte";
  import TestTree from "./TestTree.svelte";
  import type { GoldenTestEntry, GoldenTestFileData } from "./types";

  /**
   * The review workspace: the tree of the `tests/` folder of a mod on the left,
   * the source of the selected test file with its screenshots in the middle.
   *
   * The page owns the data -- the topics, the RPCs and the header with the mod
   * selector -- and this component renders it. A page with fixed mock data (the
   * demo of the dev tools) therefore renders the very same workspace.
   */
  type Props = {
    /** Browsed mod, a new mod starts with no folder open */
    modName: string;
    /** Folder path relative to tests/ -> its immediate children, '' is the root */
    dirs: Record<string, GoldenTestEntry[]>;
    /** Path of the selected file, relative to tests/ */
    selected: string;
    /** The selected test file, empty until the page has one */
    data: GoldenTestFileData;
    /** Whether the screenshots are shown next to the source */
    showImages: boolean;
    /** Message of the tree column while no mod is browsed, empty to show the tree */
    empty?: string;
    /** Folders open on mount */
    initialExpanded?: string[];
    /** Build the URL of a screenshot, defaults to the image route of the mod */
    resolveImage?: (path: string) => string;
    onSelect: (path: string) => void;
    onSubFolder: (path: string) => void;
    onUnsubFolder: (path: string) => void;
    onRefresh: () => void;
  };

  let {
    modName,
    dirs,
    selected,
    data,
    showImages = $bindable(),
    empty = "",
    initialExpanded = [],
    resolveImage,
    onSelect,
    onSubFolder,
    onUnsubFolder,
    onRefresh,
  }: Props = $props();
</script>

<!--
  The tree keeps its own column at 224px: it only has to hold test file names,
  and every pixel it gives up goes to the source and the screenshots.

  The page runs without the navigation column, so the width floor is what keeps
  the three columns readable (224 tree + 420 source + 220 screenshots). Below
  it the page scrolls sideways and shows a slice of the same layout.
-->
<div class="flex h-full min-h-0 w-full min-w-[864px] flex-col bg-background">
  <div class="flex min-h-0 flex-1">
    <div class="w-56 shrink-0 overflow-auto border-r border-border">
      {#if empty !== ""}
        <div class="p-3 text-xs text-muted-foreground">
          {empty}
        </div>
      {:else}
        <!-- A new mod starts with no folder open -->
        {#key modName}
          <TestTree {dirs} {selected} {initialExpanded} {onSelect} {onSubFolder} {onUnsubFolder} />
        {/key}
      {/if}
    </div>

    <div class="flex min-h-0 min-w-0 flex-1 flex-col">
      <!-- The toolbar belongs to the file being reviewed, so it sits over the
           source and the screenshots only: its first row is as tall as the
           filter box of the tree (h-13 = 8 + 36 + 8), which puts the two
           headers on one line and leaves every pixel of height to the tree.
           The mod selector lives in the app header, next to the title. -->
      <div class="flex h-13 shrink-0 items-center gap-2 border-b border-border px-2">
        <span
          class="min-w-0 flex-1 overflow-hidden font-mono text-xs whitespace-nowrap text-muted-foreground"
          title={selected}
        >
          {selected || "—"}
        </span>
        <Toggle
          bind:pressed={showImages}
          variant="outline"
          size="sm"
          aria-label={t.GoldenTest.ShowImages()}
          title={t.GoldenTest.ShowImages()}
        >
          <Images class="size-3.5" />
          <span class="text-xs">{t.GoldenTest.ShowImages()}</span>
        </Toggle>
        <Button variant="outline" size="sm" disabled={selected === ""} onclick={onRefresh}>
          <RefreshCw class="size-3.5" />
          <span class="text-xs">{t.GoldenTest.Refresh()}</span>
        </Button>
      </div>

      <div class="flex min-h-0 flex-1 flex-col">
        <SourceView {data} {showImages} {resolveImage} />
      </div>
    </div>
  </div>
</div>

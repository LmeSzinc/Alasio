<script lang="ts">
  import { SvelteSet } from "svelte/reactivity";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import FileCode from "@lucide/svelte/icons/file-code";
  import Folder from "@lucide/svelte/icons/folder";
  import FolderOpen from "@lucide/svelte/icons/folder-open";
  import SidebarRowIndicator from "$lib/components/sidebar-accordion/SidebarRowIndicator.svelte";
  import { sidebarRowClass } from "$lib/components/sidebar-accordion/row";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils.js";
  import { filterTree } from "./tree";
  import type { GoldenTestEntry } from "./types";

  /**
   * The file tree of the browsed mod.
   *
   * The backend sends one folder level at a time: expanding a folder asks for
   * its children, collapsing it releases them (and stops the backend from
   * scanning it). The entries of the folders the page has open are kept by the
   * backend, so this component only holds which folders are open.
   */
  type Props = {
    /** Folder path relative to tests/ -> its immediate children, '' is the root */
    dirs: Record<string, GoldenTestEntry[]>;
    /** Path of the selected file, relative to tests/ */
    selected: string;
    onSelect: (path: string) => void;
    onSubFolder: (path: string) => void;
    onUnsubFolder: (path: string) => void;
  };

  let { dirs, selected, onSelect, onSubFolder, onUnsubFolder }: Props = $props();

  const expanded = new SvelteSet<string>();

  /** Filter typed in the box above the tree; it only sees the loaded levels */
  let query = $state("");
  const visible = $derived(filterTree(dirs, query));

  function collapse(path: string) {
    const prefix = `${path}/`;
    for (const open of [...expanded]) {
      if (open === path || open.startsWith(prefix)) expanded.delete(open);
    }
    onUnsubFolder(path);
  }

  function toggle(path: string) {
    if (expanded.has(path)) {
      collapse(path);
      return;
    }
    expanded.add(path);
    // Always ask for the level: the release of a collapse is asynchronous, so
    // the children of the folder may already be gone from the view
    onSubFolder(path);
  }
</script>

{#snippet node(entry: GoldenTestEntry, depth: number)}
  {@const open = entry.is_dir === true && expanded.has(entry.path)}
  {@const active = entry.is_dir !== true && selected === entry.path}
  <!-- The metrics and the highlight of a row are the ones of the dev nav (see
       SidebarRow): the tree reads as one list with the nav next to it. gap-1
       puts the icon off the label; the indicator carries no margin of its own,
       so its bar sits close to the icon. -->
  <button
    type="button"
    class={cn(
      "flex w-full items-center text-left",
      sidebarRowClass,
      "gap-1 text-sm",
      "hover:bg-accent dark:hover:bg-accent hover:text-primary hover:underline",
      active ? "text-primary font-semibold" : "text-foreground font-medium",
    )}
    style:padding-left="{depth * 12 + 4}px"
    title={entry.path}
    onclick={() => (entry.is_dir === true ? toggle(entry.path) : onSelect(entry.path))}
  >
    <SidebarRowIndicator {active} class="mr-0" />
    {#if entry.is_dir === true}
      <ChevronRight class={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
      {#if open}
        <FolderOpen class="text-muted-foreground size-3.5 shrink-0" />
      {:else}
        <Folder class="text-muted-foreground size-3.5 shrink-0" />
      {/if}
    {:else}
      <span class="size-3.5 shrink-0"></span>
      <FileCode class="text-muted-foreground size-3.5 shrink-0" />
    {/if}
    <span class="ml-1 truncate">{entry.name}</span>
  </button>
  {#if open}
    {#each visible[entry.path] ?? [] as child (child.path)}
      {@render node(child, depth + 1)}
    {/each}
  {/if}
{/snippet}

<div class="border-border bg-background sticky top-0 z-10 border-b p-2">
  <input
    type="search"
    class="border-input placeholder:text-muted-foreground h-9 w-full rounded border bg-transparent px-2 text-xs outline-none"
    placeholder={t.GoldenTest.FilterTests()}
    aria-label={t.GoldenTest.FilterTests()}
    bind:value={query}
  />
</div>

<div class="flex flex-col pb-2">
  {#each visible[""] ?? [] as entry (entry.path)}
    {@render node(entry, 0)}
  {/each}
</div>

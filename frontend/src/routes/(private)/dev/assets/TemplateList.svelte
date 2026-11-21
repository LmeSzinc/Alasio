<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils";
  import TemplateImage from "./TemplateImage.svelte";
  import { type TemplateSelectionItem, assetSelection, templateSelection } from "./selected.svelte";
  import type { MetaAsset, MetaTemplate } from "./types";

  let {
    asset,
    mod_name,
  }: {
    /** The asset whose templates are being viewed. Null if none or multiple assets are selected. */
    asset: MetaAsset | null;
    mod_name: string;
  } = $props();

  // Defines the order and existence of language groups.
  const LANG_ORDER = ["", "cn", "en", "jp", "tw"];

  // Derived state to group and sort templates by language.
  const groupedTemplates = $derived.by<{ lang: string; templates: MetaTemplate[] }[]>(() => {
    if (!asset?.templates) return [];
    const groups = new Map<string, MetaTemplate[]>();
    LANG_ORDER.forEach((lang) => groups.set(lang, [])); // Initialize all groups
    for (const template of asset.templates) {
      if (groups.has(template.lang)) {
        groups.get(template.lang)?.push(template);
      }
    }
    return LANG_ORDER.map((lang) => ({ lang, templates: groups.get(lang) || [] }));
  });

  // Create a flat list of all visible templates for range selection.
  const allItems = $derived<TemplateSelectionItem[]>(
    asset?.templates.map((t) => ({ type: "template", file: t.file })) || [],
  );

  /**
   * Handles selection logic for templates using the global templateSelection state.
   */
  function handleTemplateSelect(template: MetaTemplate, event: MouseEvent): void {
    const item: TemplateSelectionItem = { type: "template", file: template.file };

    if (event.shiftKey) {
      templateSelection.selectRange(allItems, item);
    } else if (event.ctrlKey || event.metaKey) {
      templateSelection.toggle(item);
    } else {
      templateSelection.select(item);
    }
  }

  // Ref for the container to detect background clicks.
  let containerRef: HTMLDivElement | null = $state(null);
  function handleBackgroundClick(event: MouseEvent): void {
    if (event.target === containerRef) {
      templateSelection.clear();
    }
  }

  function getLangDisplayName(lang: string): string {
    if (lang === "") return "Shared";
    return lang.toUpperCase();
  }
</script>

<ScrollArea class="h-full">
  <div class="p-4" bind:this={containerRef} onclick={handleBackgroundClick} role="presentation">
    {#if !asset}
      <!-- Empty state: shown when 0 or >1 assets are selected -->
      <div class="flex h-full items-center justify-center pt-10 text-sm text-muted-foreground">
        {#if assetSelection.count === 0}
          Select an asset to view its templates.
        {:else}
          {assetSelection.count} assets selected. Please select a single asset to view templates.
        {/if}
      </div>
    {:else}
      <!-- Header: shown when exactly 1 asset is selected -->
      <div class="mb-4 border-b pb-2">
        <h2 class="text-xl font-bold tracking-tight">{asset.name}</h2>
        <p class="text-sm text-muted-foreground">
          {asset.templates.length} template{asset.templates.length !== 1 ? "s" : ""} found.
        </p>
      </div>

      {#if asset.templates.length === 0}
        <div class="flex h-full items-center justify-center pt-10 text-sm text-muted-foreground">
          This asset has no templates.
        </div>
      {:else}
        {#each groupedTemplates as group (group.lang)}
          <div class="mb-6">
            <h3 class="mb-2 text-lg font-semibold tracking-tight text-foreground">
              {getLangDisplayName(group.lang)}
            </h3>
            <div class="flex min-h-24 flex-wrap gap-2 rounded-md border bg-muted/30 p-3">
              {#if group.templates.length === 0}
                <p class="self-center p-2 text-xs text-muted-foreground">No templates for this language.</p>
              {:else}
                {#each group.templates as template (template.file)}
                  {@const isSelected = templateSelection.isSelected({ type: "template", file: template.file })}
                  <button
                    class={cn(
                      "rounded-sm ring-offset-2 ring-offset-background outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      isSelected && "ring-2 ring-blue-500",
                    )}
                    onclick={(e) => handleTemplateSelect(template, e)}
                  >
                    <TemplateImage {template} {mod_name} showFrameInfo={true} />
                  </button>
                {/each}
              {/if}
            </div>
          </div>
        {/each}
      {/if}
    {/if}
  </div>
</ScrollArea>

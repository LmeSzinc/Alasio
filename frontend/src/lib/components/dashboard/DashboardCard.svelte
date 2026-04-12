<script lang="ts">
  import { t } from "$lib/i18n";
  import { elementSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import { ChevronDown, ChevronUp } from "@lucide/svelte";
  import DashboardItem from "./DashboardItem.svelte";
  import type { DashboardArgData } from "./utils";

  let {
    items,
    class: className,
  }: {
    items: Record<string, Record<string, DashboardArgData>>;
    class?: string;
  } = $props();

  let isExpanded = $state(false);
  let containerSize = $state({ width: 0, height: 0 });

  const groups = $derived(Object.entries(items || {}));
  const displayGroups = $derived(isExpanded ? groups : groups.slice(0, 1));

  // Auto collapse dashboard when focus is lost
  function onfocusout(e: FocusEvent) {
    const container = e.currentTarget as HTMLElement;
    if (isExpanded && !container?.contains(e.relatedTarget as Node)) {
      isExpanded = false;
    }
  }
</script>

<div class={cn("relative w-full", isExpanded ? "z-10" : "", className)} {onfocusout}>
  <!-- Spacer to maintain the 2-row height in document flow -->
  <!-- 2 rows (approx 42px each) + gap (12px) + padding (32px) ~= 128px -->
  <div class="h-32 w-full shrink-0"></div>

  <!-- Actual dashboard content -->
  <!-- Expand to 5 rows at max -->
  <!-- 5 rows (approx 42px each) + gap (12px) + padding (32px) ~= 290px -->
  <div
    role="presentation"
    tabindex="-1"
    class={cn(
      "bg-card neushadow absolute top-0 left-0 w-full overflow-hidden rounded-xl outline-none",
      isExpanded ? "max-h-72.5 min-h-32 shadow-2xl ring-1 ring-black/5" : "h-32",
    )}
  >
    <div
      class={cn("flex flex-col gap-4 p-4", isExpanded ? "max-h-72.5 overflow-y-auto" : "")}
      use:elementSize={containerSize}
    >
      {#each displayGroups as [groupKey, groupItems], index}
        {#if index > 0}
          <hr />
        {/if}
        <div class="grid grid-cols-[repeat(auto-fill,minmax(8.75rem,1fr))] gap-x-2 gap-y-3">
          {#each Object.values(groupItems) as data}
            <DashboardItem {data} class="w-auto" />
          {/each}
        </div>
      {/each}
    </div>
    <!-- Toggle button -->
    <!-- Show toggle button if there are multiple groups or the first group is large -->
    {#if groups.length > 1 || containerSize.height > 128}
      <button
        onclick={() => (isExpanded = !isExpanded)}
        class="bg-card hover:bg-accent hover:text-accent-foreground absolute right-2 bottom-2 flex h-6 w-6 items-center justify-center rounded-full shadow-sm focus-visible:ring-2 focus-visible:outline-none"
        title={isExpanded ? t.Dashboard.Collapse() : t.Dashboard.Expand()}
      >
        {#if isExpanded}
          <ChevronUp class="h-4 w-4" />
        {:else}
          <ChevronDown class="h-4 w-4" />
        {/if}
      </button>
    {/if}
  </div>
</div>

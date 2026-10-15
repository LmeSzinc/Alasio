<script lang="ts">
  import Minus from "@lucide/svelte/icons/minus";
  import Plus from "@lucide/svelte/icons/plus";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { type HistoryItem, prettyVersion } from "./utils";

  type $props = {
    item: HistoryItem;
  };
  let { item }: $props = $props();

  // whether the detail is expanded
  let expanded = $state(false);

  function toggleDetail() {
    expanded = !expanded;
  }

  function formatTime(time: number): string {
    return new Date(time * 1000).toLocaleString();
  }
</script>

<div>
  <!-- every commit is one row of the same height: the expand button of a
       commit that carries a detail is taller than its text, the row would
       grow with it and the list would look uneven. The rows carry no separator
       of their own: only the header of the table has one below it -->
  <div class="grid h-8 grid-cols-[90px_110px_160px_minmax(0,1fr)_28px] items-center gap-x-2">
    <span class="truncate font-mono text-xs" title={item.version}>{prettyVersion(item.version)}</span>
    <span class="truncate text-sm" title={item.author}>{item.author}</span>
    <span class="text-muted-foreground truncate text-xs">{formatTime(item.time)}</span>
    <span class="min-w-0 truncate text-sm" title={item.title}>{item.title}</span>
    <div class="flex justify-end">
      {#if item.detail}
        <Button
          variant="ghost"
          size="icon-sm"
          title={expanded ? t.Mod.CollapseDetail() : t.Mod.ExpandDetail()}
          onclick={toggleDetail}
        >
          {#if expanded}
            <Minus />
          {:else}
            <Plus />
          {/if}
        </Button>
      {/if}
    </div>
  </div>
  {#if expanded}
    <div class="text-muted-foreground mt-1 ml-[92px] space-y-1 pb-2 text-sm">
      <div class="font-medium whitespace-pre-wrap">{item.title}</div>
      <div class="whitespace-pre-wrap">{item.detail}</div>
    </div>
  {/if}
</div>

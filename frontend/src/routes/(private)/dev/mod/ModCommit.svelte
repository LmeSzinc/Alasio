<script lang="ts">
  import { Minus, Plus } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";

  export type HistoryItem = {
    version: string;
    author: string;
    time: number;
    title: string;
    detail: string;
  };

  type $$props = {
    item: HistoryItem;
  };
  let { item }: $$props = $props();

  const SHA1_REGEX = /^[0-9a-f]{40}$/;

  // whether the detail is expanded
  let expanded = $state(false);

  function toggleDetail() {
    expanded = !expanded;
  }

  function formatTime(time: number): string {
    return new Date(time * 1000).toLocaleString();
  }

  function displayVersion(version: string): string {
    // show the short sha1 of a commit, keep the full tag name
    return SHA1_REGEX.test(version) ? version.slice(0, 7) : version;
  }
</script>

<div class="py-1.5">
  <div class="grid grid-cols-[90px_110px_160px_minmax(0,1fr)_28px] items-center gap-x-2">
    <span class="truncate font-mono text-xs" title={item.version}>{displayVersion(item.version)}</span>
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
    <div class="text-muted-foreground mt-1 ml-[92px] space-y-1 text-sm">
      <div class="font-medium whitespace-pre-wrap">{item.title}</div>
      <div class="whitespace-pre-wrap">{item.detail}</div>
    </div>
  {/if}
</div>

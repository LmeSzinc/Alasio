<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import ModCommit, { type HistoryItem } from "./ModCommit.svelte";

  export type ModOption = {
    value: string;
    label: string;
  };
  export type ModHistoryData = Record<string, { data?: HistoryItem[]; error?: string }>;

  type $$props = {
    class?: string;
    mods?: ModOption[];
    history?: ModHistoryData;
  };
  let { class: className, mods: modsProp, history: historyProp }: $$props = $props();

  // Number of commits shown before expanding
  const PREVIEW_COUNT = 3;
  const SHA1_REGEX = /^[0-9a-f]{40}$/;

  const modListTopic = useTopic<ModOption[]>("ModList");
  const modHistoryTopic = useTopic<ModHistoryData>("ModHistory");

  // data from props for testing, otherwise from topics
  const mods = $derived(modsProp ?? modListTopic.data ?? []);
  const historyData = $derived(historyProp ?? modHistoryTopic.data);

  // per mod: whether all commits are expanded
  const showAll = $state<Record<string, boolean>>({});

  function toggleShowAll(mod: string) {
    showAll[mod] = !showAll[mod];
  }
</script>

{#if mods.length === 0}
  <div class="text-muted-foreground flex items-center justify-center rounded-lg border-2 border-dashed py-16 text-sm">
    {t.Mod.NoMod()}
  </div>
{:else}
  <div class={cn("flex flex-col gap-4", className)}>
    {#each mods as mod (mod.value)}
      {@const history = historyData?.[mod.value]}
      {@const items = history?.data ?? []}
      {@const visibleItems = showAll[mod.value] ? items : items.slice(0, PREVIEW_COUNT)}
      {@const version = items[0]?.version ?? ""}
      <Card.Root class="flex flex-col">
        <Card.Header class="flex flex-row items-center justify-between gap-2">
          <Card.Title class="truncate">{mod.label}</Card.Title>
          {#if version}
            <Badge variant="secondary" class="shrink-0 font-mono" title={version}>
              {SHA1_REGEX.test(version) ? version.slice(0, 7) : version}
            </Badge>
          {/if}
        </Card.Header>
        <Card.Content class="grow">
          {#if history?.error}
            <div class="text-destructive text-sm">{history.error}</div>
          {:else if items.length === 0}
            <div class="text-muted-foreground text-sm">{t.Mod.NoHistory()}</div>
          {:else}
            <div
              class="text-muted-foreground mb-1 grid grid-cols-[90px_110px_160px_minmax(0,1fr)_28px] items-center gap-x-2 border-b pb-1 text-xs"
            >
              <span>{t.Mod.Version()}</span>
              <span>{t.Mod.Author()}</span>
              <span>{t.Mod.Time()}</span>
              <span>{t.Mod.CommitTitle()}</span>
              <span></span>
            </div>
            <div class="divide-border divide-y">
              {#each visibleItems as item (item.version)}
                <ModCommit {item} />
              {/each}
            </div>
            {#if items.length > PREVIEW_COUNT}
              <div class="mt-2 flex justify-center">
                <Button variant="outline" size="sm" onclick={() => toggleShowAll(mod.value)}>
                  {#if showAll[mod.value]}
                    {t.Mod.CollapseAll()}
                  {:else}
                    {t.Mod.ExpandAll()}
                  {/if}
                </Button>
              </div>
            {/if}
          {/if}
        </Card.Content>
      </Card.Root>
    {/each}
  </div>
{/if}

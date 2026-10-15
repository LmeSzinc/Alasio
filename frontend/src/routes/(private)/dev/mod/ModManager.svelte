<script lang="ts">
  import type { UpdateTopicLike } from "$lib/components/aside/types";
  import ModInfo from "$lib/components/update/ModInfo.svelte";
  import UpdateControl from "$lib/components/update/UpdateControl.svelte";
  import { type ModHistoryData, type ModOption } from "$lib/components/update/utils";
  import { t } from "$lib/i18n";
  import { cardWidth } from "$lib/use/cardWidth.svelte";
  import { elementSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";

  type $props = {
    class?: string;
    mods?: ModOption[];
    history?: ModHistoryData;
    update?: UpdateTopicLike;
  };
  let { class: className, mods: modsProp, history: historyProp, update: updateProp }: $props = $props();

  const modListTopic = useTopic<ModOption[]>("ModList");
  const modHistoryTopic = useTopic<ModHistoryData>("ModHistory");
  // the update flow of every mod is driven by UpdateControl, which reads the
  // Update topic itself (or the prop the dev page passes down)
  const updateTopic = useTopic<UpdateTopicLike>("Update");

  // data from props for testing, otherwise from topics
  const mods = $derived(modsProp ?? modListTopic.data ?? []);
  const historyData = $derived(historyProp ?? modHistoryTopic.data);
  const updateData = $derived(updateProp ?? updateTopic.data);

  function updateInfo(mod: string) {
    return updateData?.[mod];
  }

  // the cards follow the width of the cards of the config pages, see cardWidth()
  let containerSize = $state({ width: 0, height: 0 });
  const cardClass = $derived(cardWidth(containerSize.width));
</script>

<!-- the outer box measures the width the cards may use, the inner box is the
     centered column: the cards, the update line and the placeholder all share
     it, so every edge lines up -->
<div use:elementSize={containerSize} class={cn("flex flex-col", className)}>
  <div class={cn("mx-auto flex w-full flex-col gap-4", cardClass)}>
    {#if mods.length === 0}
      <div
        class="text-muted-foreground flex items-center justify-center rounded-lg border-2 border-dashed py-16 text-sm"
      >
        {t.Mod.NoMod()}
      </div>
    {:else}
      <UpdateControl {mods} update={updateData} />
      {#each mods as mod (mod.value)}
        <ModInfo {mod} history={historyData?.[mod.value]} info={updateInfo(mod.value)} />
      {/each}
    {/if}
  </div>
</div>

<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import ConfigItem from "$lib/components/aside/ConfigItem.svelte";
  import type { ConfigLike, RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  // Every worker state, in display order (the array the WORKER_STATE type is
  // derived from): this page can never miss a state
  import { WORKER_STATES } from "$lib/components/aside/types";
  import * as Card from "$lib/components/ui/card";

  // The three configs of every preview aside: a short name, the two-line wrap
  // the sidebar meets in the app and a long one. The data is fixed instead of
  // the live ConfigScan topic: a preview following the real list would change
  // under the reader and could not show every state on one page.
  const ASIDE_CONFIGS: ConfigLike[] = [
    { id: 1, name: "alas", mod: "alas", gid: 0, iid: 1 },
    { id: 2, name: "example_mod2", mod: "alas", gid: 0, iid: 2 },
    { id: 3, name: "main_configuration_with_a_long_name", mod: "alas", gid: 0, iid: 3 },
  ];

  type Flow = RestartTopicLike["flow"];

  // The two cards of the page: the same worker states under the two flows that
  // change what the item draws. "Normal" is the ordinary sidebar (the worker
  // state badge alone, bottom right); "Startup Update" is the first full update
  // of the process start, which adds the updating marker in the top right
  // corner and leaves the state badge in place.
  const CARDS: { title: string; note: string; flow: Flow }[] = [
    {
      title: "Normal States",
      note: "No flow owns the backend: each config draws the worker state it reports, in the bottom right corner.",
      flow: "",
    },
    {
      title: "Startup Update",
      note: "The startup flow is applying the first full update (check, download, apply): the updating marker sits in the top right corner of every config, the worker state stays in the bottom right one.",
      flow: "startup",
    },
  ];
</script>

{#snippet MockAside(workerState: WORKER_STATE, flow: Flow)}
  <!-- The column of the real ConfigAside: w-20, one tile per config, the tile
       wrappers carrying the same px-1 the sidebar list uses. The ring stands in
       for the sidebar edge, so the column is readable on the card background. -->
  <aside
    class="flex w-20 flex-col gap-1 rounded-md p-1 ring-1 ring-border"
    aria-label="Config column, state {workerState}"
  >
    {#each ASIDE_CONFIGS as config (config.id)}
      <div class="px-1">
        <ConfigItem {config} state={workerState} {flow} active={config.id === 2} />
      </div>
    {/each}
  </aside>
{/snippet}

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <h1 class="text-3xl font-bold">Config Aside Debug Page</h1>
  <p class="text-xs text-muted-foreground">
    Every worker state in a vertical config column of three configs. The middle config of each column is the selected
    one, so both the active and the inactive look of a state are visible.
  </p>

  {#each CARDS as card (card.title)}
    <!-- shrink-0: the page is a column flex container of a fixed height, without
         it the cards are squeezed into the viewport and their content is
         clipped by the card's own overflow-hidden -->
    <Card.Root class="neushadow shrink-0 border-none">
      <Card.Header>
        <Card.Title>{card.title}</Card.Title>
        <p class="text-xs text-muted-foreground">{card.note}</p>
      </Card.Header>
      <Card.Content>
        <!-- The aside is 80px wide and vertical, so the columns pack with a
             narrow horizontal gap (a state name still fits above each one) -->
        <div class="flex flex-wrap items-start gap-x-2 gap-y-3">
          {#each WORKER_STATES as workerState (workerState)}
            <div class="flex w-20 flex-col items-center gap-1">
              <!-- min-h-8: the longest state name wraps at this width, a fixed
                   reserve keeps the asides of one row on the same line -->
              <div class="flex min-h-8 w-full items-center justify-center font-mono text-xs text-muted-foreground">
                {workerState}
              </div>
              {@render MockAside(workerState, card.flow)}
            </div>
          {/each}
        </div>
      </Card.Content>
    </Card.Root>
  {/each}
</div>

<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import ArgCard from "$lib/components/arg/ArgCard.svelte";
  import type { ArgData, CardData, InfoData } from "$lib/components/arg/utils.svelte";

  // --- Mock helpers ---
  const noop = () => {};

  function info(group: string, name: string, help?: string): InfoData {
    return { group, arg: "_info", card: `card-${group}`, name, help };
  }

  function arg(group: string, argName: string, dt: string, value: any, extra: Record<string, any> = {}): ArgData {
    return { task: "Debug", group, arg: argName, dt, value, ...extra };
  }

  function makeCard(infoData: InfoData, groups: Record<string, Record<string, ArgData>> = {}): CardData {
    // Object spread of an index-signature type does not propagate the index signature,
    // so cast the literal to CardData (comparable in either direction).
    return { _info: infoData, ...groups } as CardData;
  }

  // Realistic Scheduler group: Enable/NextRun are rendered as the card badge,
  // extra Scheduler args (e.g. ServerUpdate) are rendered inside the card header.
  function scheduler(): Record<string, ArgData> {
    return {
      Enable: arg("Scheduler", "Enable", "enable", true, { option: ["true", "false"] }),
      NextRun: arg("Scheduler", "NextRun", "datetime", "2020-01-01T00:00:00Z"),
      ServerUpdate: arg("Scheduler", "ServerUpdate", "select", "00:00", { option: ["00:00", "04:00", "12:00"] }),
    };
  }

  // --- 1. Header test: _info.help × Scheduler group, 2×2 = 4 combinations ---
  const headerCases = $state<CardData[]>([
    makeCard(info("HeaderNoHelpNoScheduler", "Header: No Help, No Scheduler"), {
      Sample: { Name: arg("Sample", "Name", "input", "value", { name: "Name" }) },
    }),
    makeCard(info("HeaderHelpNoScheduler", "Header: Help, No Scheduler", "This card has a help text"), {
      Sample: { Name: arg("Sample", "Name", "input", "value", { name: "Name" }) },
    }),
    makeCard(info("HeaderNoHelpScheduler", "Header: No Help, Scheduler"), {
      Scheduler: scheduler(),
      Sample: { Name: arg("Sample", "Name", "input", "value", { name: "Name" }) },
    }),
    makeCard(info("HeaderHelpScheduler", "Header: Help, Scheduler", "This card has both help and a Scheduler group"), {
      Scheduler: scheduler(),
      Sample: { Name: arg("Sample", "Name", "input", "value", { name: "Name" }) },
    }),
  ]);

  // --- 2. Single group test: 0 / 1 / 2 / 3 args in one group ---
  const singleGroupCases = $state<CardData[]>([
    makeCard(info("Single0", "Single Group · 0 Args"), { Group: {} }),
    makeCard(info("Single1", "Single Group · 1 Arg"), {
      Group: { Name: arg("Group", "Name", "input", "value", { name: "Name" }) },
    }),
    makeCard(info("Single2", "Single Group · 2 Args"), {
      Group: {
        Name: arg("Group", "Name", "input", "value", { name: "Name" }),
        Enabled: arg("Group", "Enabled", "checkbox", true, { name: "Enabled" }),
      },
    }),
    makeCard(info("Single3", "Single Group · 3 Args"), {
      Group: {
        Name: arg("Group", "Name", "input", "value", { name: "Name" }),
        Mode: arg("Group", "Mode", "select", "auto", { name: "Mode", option: ["auto", "manual"] }),
        Notes: arg("Group", "Notes", "textarea", "notes", { name: "Notes" }),
      },
    }),
  ]);

  // --- 3. Multi group test: 0 / 1 / 2 / 3 groups, 3 args each ---
  function demoGroup(groupName: string, seed: number): Record<string, ArgData> {
    return {
      [`ArgA${seed}`]: arg(groupName, `ArgA${seed}`, "input", `value-${seed}`, { name: "Arg A" }),
      [`ArgB${seed}`]: arg(groupName, `ArgB${seed}`, "input-int", seed * 10, { name: "Arg B", ge: 0, le: 100 }),
      [`ArgC${seed}`]: arg(groupName, `ArgC${seed}`, "select", "option-1", {
        name: "Arg C",
        option: ["option-1", "option-2"],
      }),
    };
  }
  const multiGroupCases = $state<CardData[]>([
    makeCard(info("Multi0", "Multi Group · 0 Groups")),
    makeCard(info("Multi1", "Multi Group · 1 Group"), { Group1: demoGroup("Group1", 1) }),
    makeCard(info("Multi2", "Multi Group · 2 Groups"), {
      Group1: demoGroup("Group1", 1),
      Group2: demoGroup("Group2", 2),
    }),
    makeCard(info("Multi3", "Multi Group · 3 Groups"), {
      Group1: demoGroup("Group1", 1),
      Group2: demoGroup("Group2", 2),
      Group3: demoGroup("Group3", 3),
    }),
  ]);
</script>

<div class="container mx-auto flex flex-col gap-8 overflow-auto p-6 pb-20">
  <h1 class="text-3xl font-bold">Config Display Test</h1>
  <p class="text-muted-foreground">
    ArgCard rendering under different data shapes: card header combinations, single-group arg counts, and multi-group
    counts.
  </p>

  <!-- 1. Header test -->
  <section class="space-y-3">
    <h2 class="text-xl font-semibold">1. Header Test</h2>
    <p class="text-muted-foreground text-sm">
      <code class="bg-muted rounded px-1">_info.help</code> × Scheduler group, 2×2 = 4 combinations. Scheduler
      <code class="bg-muted rounded px-1">Enable</code> / <code class="bg-muted rounded px-1">NextRun</code> are
      rendered as the card badge; extra Scheduler args (e.g. <code class="bg-muted rounded px-1">ServerUpdate</code>)
      appear inside the header.
    </p>
    <div class="space-y-4">
      {#each headerCases as _, i (i)}
        <ArgCard
          bind:cardData={headerCases[i]}
          parentWidth={1800}
          handleEdit={noop}
          handleReset={noop}
          class="max-w-240"
        />
      {/each}
    </div>
  </section>

  <!-- 2. Single group test -->
  <section class="space-y-3">
    <h2 class="text-xl font-semibold">2. Single Group Test</h2>
    <p class="text-muted-foreground text-sm">One group with 0 / 1 / 2 / 3 args.</p>
    <div class="space-y-4">
      {#each singleGroupCases as _, i (i)}
        <ArgCard
          bind:cardData={singleGroupCases[i]}
          parentWidth={1800}
          handleEdit={noop}
          handleReset={noop}
          class="max-w-240"
        />
      {/each}
    </div>
  </section>

  <!-- 3. Multi group test -->
  <section class="space-y-3">
    <h2 class="text-xl font-semibold">3. Multi Group Test</h2>
    <p class="text-muted-foreground text-sm">0 / 1 / 2 / 3 groups, 3 args each.</p>
    <div class="space-y-4">
      {#each multiGroupCases as _, i (i)}
        <ArgCard
          bind:cardData={multiGroupCases[i]}
          parentWidth={1800}
          handleEdit={noop}
          handleReset={noop}
          class="max-w-240"
        />
      {/each}
    </div>
  </section>
</div>

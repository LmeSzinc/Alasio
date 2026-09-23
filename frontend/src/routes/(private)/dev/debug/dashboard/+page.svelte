<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import DashboardCard from "$lib/components/dashboard/DashboardCard.svelte";

  // Mock data based on ExampleMod/module/config/dashboard/dashboard_config.json
  // Format: groupName -> argName -> ArgData
  const baseItems: Record<string, Record<string, ArgData>> = {
    Oil: {
      _info: {
        group: "Oil",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-total",
        dashboard: "Total",
        dashboard_color: "#7f7f7f",
        name: "Oil",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Oil",
        arg: "Value",
        dt: "input-int",
        value: 1234,
        ge: 0,
        le: 25000,
      },
      Time: {
        task: "Dashboard",
        group: "Oil",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 5 * 60 * 1000).toISOString().replace("Z", ""),
      },
    },
    Gems: {
      _info: {
        group: "Gems",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-value",
        dashboard: "Amount",
        dashboard_color: "#eb8efe",
        name: "Gems",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Gems",
        arg: "Value",
        dt: "input-int",
        value: 500,
        ge: 0,
      },
      Time: {
        task: "Dashboard",
        group: "Gems",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString().replace("Z", ""),
      },
    },
    Progress: {
      _info: {
        group: "Progress",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-progress",
        dashboard: "Progress",
        name: "Campaign",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Progress",
        arg: "Value",
        dt: "input-float",
        value: 45.67,
      },
      Time: {
        task: "Dashboard",
        group: "Progress",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 10 * 1000).toISOString().replace("Z", ""),
      },
    },
    Planner: {
      _info: {
        group: "Planner",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-planner",
        dashboard: "Planner",
        name: "Research",
        value: "",
      },
      Progress: {
        task: "Dashboard",
        group: "Planner",
        arg: "Progress",
        dt: "input-int",
        value: 80,
      },
      Eta: {
        task: "Dashboard",
        group: "Planner",
        arg: "Eta",
        dt: "input",
        value: "01:23:45",
      },
      Time: {
        task: "Dashboard",
        group: "Planner",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 30 * 1000).toISOString().replace("Z", ""),
      },
    },
    // Additional items to test expansion (simple Amount type)
    Coins: {
      _info: {
        group: "Coins",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-value",
        dashboard: "Amount",
        dashboard_color: "#ffd700",
        name: "Coins",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Coins",
        arg: "Value",
        dt: "input-int",
        value: 154321,
      },
      Time: {
        task: "Dashboard",
        group: "Coins",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 15 * 60 * 1000).toISOString(),
      },
    },
    Exp: {
      _info: {
        group: "Exp",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-value",
        dashboard: "Amount",
        dashboard_color: "#4caf50",
        name: "Exp",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Exp",
        arg: "Value",
        dt: "input-int",
        value: 9999,
      },
      Time: {
        task: "Dashboard",
        group: "Exp",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
      },
    },
    Medals: {
      _info: {
        group: "Medals",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-value",
        dashboard: "Amount",
        dashboard_color: "#f44336",
        name: "Medals",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Medals",
        arg: "Value",
        dt: "input-int",
        value: 42,
      },
      Time: {
        task: "Dashboard",
        group: "Medals",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
      },
    },
    Dorm: {
      _info: {
        group: "Dorm",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-total",
        dashboard: "DynamicTotal",
        dashboard_color: "#ff9800",
        name: "Dorm Food",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Dorm",
        arg: "Value",
        dt: "input-int",
        value: 5000,
      },
      Total: {
        task: "Dashboard",
        group: "Dorm",
        arg: "Total",
        dt: "static",
        value: 40000,
      },
      Time: {
        task: "Dashboard",
        group: "Dorm",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
      },
    },
    Commission: {
      _info: {
        group: "Commission",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-planner",
        dashboard: "Planner",
        dashboard_color: "#2196f3",
        name: "Commission",
        value: "",
      },
      Progress: {
        task: "Dashboard",
        group: "Commission",
        arg: "Progress",
        dt: "input-int",
        value: 50,
      },
      Eta: {
        task: "Dashboard",
        group: "Commission",
        arg: "Eta",
        dt: "input",
        value: "02:15:00",
      },
      Time: {
        task: "Dashboard",
        group: "Commission",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 5 * 1000).toISOString(),
      },
    },
  };

  // The same items under distinct keys, more items than a single row holds
  const mockItemsMany: Record<string, Record<string, ArgData>> = Object.fromEntries(
    [1, 2, 3].flatMap((copy) => Object.entries(baseItems).map(([name, data]) => [`${name}-${copy}`, data])),
  );

  // The three value forms, one item each: a plain amount, an amount over its
  // limit (`Total`), and an amount over a dynamic total. Names and times are
  // long enough to squeeze the info line as well.
  const valueFormItems: Record<string, Record<string, ArgData>> = {
    Amount: {
      _info: {
        group: "Amount",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-value",
        dashboard: "Amount",
        dashboard_color: "#f44336",
        name: "Medals",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Amount",
        arg: "Value",
        dt: "input-int",
        value: 42,
        ge: 0,
      },
      Time: {
        task: "Dashboard",
        group: "Amount",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      },
    },
    Total: {
      _info: {
        group: "Total",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-total",
        dashboard: "Total",
        dashboard_color: "#ffd700",
        name: "Oil",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Total",
        arg: "Value",
        dt: "input-int",
        value: 1234,
        ge: 0,
        le: 25000,
      },
      Time: {
        task: "Dashboard",
        group: "Total",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      },
    },
    DynamicTotal: {
      _info: {
        group: "DynamicTotal",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-total",
        dashboard: "DynamicTotal",
        dashboard_color: "#ff9800",
        name: "Dorm Food",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "DynamicTotal",
        arg: "Value",
        dt: "input-int",
        value: 5000,
      },
      Total: {
        task: "Dashboard",
        group: "DynamicTotal",
        arg: "Total",
        dt: "static",
        value: 40000,
      },
      Time: {
        task: "Dashboard",
        group: "DynamicTotal",
        arg: "Time",
        dt: "datetime",
        value: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
      },
    },
  };

  // Card widths from the full container down to w-40, one card per step
  const widthLadder = ["w-full", "w-96", "w-80", "w-72", "w-64", "w-60", "w-56", "w-52", "w-48", "w-44", "w-40"];

  // --- Flash on Value Change ---

  // Pinned group: the mock items above, whose flash state comes from
  // `overrideFlash` (see the card below) instead of their values. They are only
  // read, so they can be the very records the cards above share.

  // Live group: the same items with their values rewritten on a timer. The
  // records are deep copied, because the writes below go through the `$state`
  // proxy and would otherwise rewrite the mock records that every other card on
  // this page shares.
  const LIVE_TICK = 800;
  const copyItems = (items: Record<string, Record<string, ArgData>>): Record<string, Record<string, ArgData>> =>
    JSON.parse(JSON.stringify(items));

  const liveItems = $state<Record<string, Record<string, ArgData>>>({
    ...copyItems(baseItems),
    // The fast item is rewritten on every tick instead of on a random fifth of
    // them: its values never stop changing, so its highlight never ends either,
    // each update restarting the second the previous one started.
    Fast: {
      _info: {
        group: "Fast",
        arg: "_info",
        task: "Dashboard",
        dt: "dashboard-value",
        dashboard: "Amount",
        dashboard_color: "#00bcd4",
        name: "Fast",
        value: "",
      },
      Value: {
        task: "Dashboard",
        group: "Fast",
        arg: "Value",
        dt: "input-int",
        value: 0,
        ge: 0,
      },
      Time: {
        task: "Dashboard",
        group: "Fast",
        arg: "Time",
        dt: "datetime",
        value: new Date().toISOString(),
      },
    },
  });

  /** A random "HH:MM:SS" duration, the form the planner displays */
  const randomDuration = () =>
    [Math.floor(Math.random() * 100), Math.floor(Math.random() * 60), Math.floor(Math.random() * 60)]
      .map((part) => String(part).padStart(2, "0"))
      .join(":");

  /** Writes random values into one live item, following its dashboard type */
  function randomizeItem(args: Record<string, ArgData>) {
    const dashboard = args._info?.dashboard ?? "";
    if (dashboard === "Planner") {
      if (args.Progress) args.Progress.value = Math.round(Math.random() * 100);
      if (args.Eta) args.Eta.value = randomDuration();
    } else if (dashboard === "Progress") {
      if (args.Value) args.Value.value = Math.round(Math.random() * 10000) / 100;
    } else if (dashboard === "Total" || dashboard === "Remain") {
      if (args.Value) args.Value.value = Math.round(Math.random() * (args.Value.le ?? 25000));
    } else if (dashboard === "DynamicTotal") {
      if (args.Value) args.Value.value = Math.round(Math.random() * (args.Total?.value ?? 40000));
    } else if (args.Value) {
      args.Value.value = Math.round(Math.random() * 999999);
    }
    // The timestamp of the update moves with the values, as it does in a real
    // dashboard update. It is not a value: it never starts a flash on its own.
    if (args.Time) args.Time.value = new Date().toISOString();
  }

  // The random group runs for as long as the page is open; the effect clears
  // the interval when the page is left.
  $effect(() => {
    const timer = setInterval(() => {
      for (const [key, args] of Object.entries(liveItems)) {
        // Every tick rewrites the fast item; each other item only on a random
        // fifth of the ticks (roughly every 4s), so a few items flash at a
        // time instead of most of them.
        if (key !== "Fast" && Math.random() >= 0.2) continue;
        randomizeItem(args);
      }
    }, LIVE_TICK);
    return () => clearInterval(timer);
  });
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4 pb-20">
  <h1 class="text-3xl font-bold">Dashboard Component Debug Page</h1>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Dashboard Overview</h2>
      <p class="text-muted-foreground text-sm">
        Testing the Dashboard component with mock data. All items are displayed, wrapped into rows of at least two
        columns.
      </p>
    </div>

    <DashboardCard items={baseItems} class="bg-card h-64 rounded-lg" />
  </section>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Flash on Value Change</h2>
      <p class="text-muted-foreground text-sm">
        An item highlights for one second when one of its values changes: the background turns
        <code class="bg-muted rounded px-1">primary</code>, the text
        <code class="bg-muted rounded px-1">primary-foreground</code>, the dot
        <code class="bg-muted rounded px-1">background</code>, and the colors transition. An update that arrives while
        the item still flashes restarts that second, so the highlight follows the last update.
      </p>
    </div>

    <div class="space-y-2">
      <h3 class="text-lg font-semibold">Pinned Flash State (overrideFlash)</h3>
      <p class="text-muted-foreground text-sm">
        <code class="bg-muted rounded px-1">overrideFlash</code> pins the flash state instead of reacting to the values: every
        item of this group is pinned lit, so the whole card can be inspected in the flash colors (the background, the text,
        the dot, the inset of the highlight) without waiting for an update. The values of this group never change.
      </p>
      <DashboardCard items={baseItems} overrideFlash={true} class="bg-card h-64 rounded-lg" />
    </div>

    <div class="space-y-2">
      <h3 class="text-lg font-semibold">Random Values</h3>
      <p class="text-muted-foreground text-sm">
        Values are rewritten every 800 ms: the fast item on every tick, each other item on a random fifth of them
        (roughly every 4 s), so only a few items flash at a time. An item written again while it still flashes stays lit
        (its second is restarted) instead of blinking off in between, which is what the fast item shows: it never ends.
      </p>
      <DashboardCard items={liveItems} class="bg-card h-64 rounded-lg" />
    </div>
  </section>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Many Items</h2>
      <p class="text-muted-foreground text-sm">
        More items than fit into the height of the card: the flow scrolls, as many items per row as their preferred
        width allows.
      </p>
    </div>

    <DashboardCard items={mockItemsMany} class="bg-card h-64 rounded-lg" />
  </section>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Narrow Container</h2>
      <p class="text-muted-foreground text-sm">
        A container too narrow for the preferred item width: items are compressed to keep two columns per row, and the
        info line degrades in one direction only: <code class="bg-muted rounded px-1">Oil - 3h ago</code> &rarr;
        <code class="bg-muted rounded px-1">Oil - 3...</code> &rarr; <code class="bg-muted rounded px-1">Oil</code>
        &rarr; <code class="bg-muted rounded px-1">O..</code> (the name yields last; the whole tail, separator included, is
        dropped once it cannot show a character of the time).
      </p>
    </div>

    <DashboardCard items={baseItems} class="bg-card h-64 w-56 rounded-lg" />

    <DashboardCard items={baseItems} class="bg-card h-64 w-40 rounded-lg" />
  </section>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Value Forms by Width</h2>
      <p class="text-muted-foreground text-sm">
        The three value forms only (Amount, Total, DynamicTotal), from the full container width down to
        <code class="bg-muted rounded px-1">w-40</code>. Both lines degrade in one direction only as the card gets
        narrower. The value line reads <code class="bg-muted rounded px-1">1234 / 25000</code> &rarr;
        <code class="bg-muted rounded px-1">1234 / 2...</code> &rarr; <code class="bg-muted rounded px-1">1234</code>
        &rarr; <code class="bg-muted rounded px-1">12...</code>, the info line
        <code class="bg-muted rounded px-1">Oil - 3h ago</code> &rarr;
        <code class="bg-muted rounded px-1">Oil - 3...</code> &rarr; <code class="bg-muted rounded px-1">Oil</code>
        &rarr; <code class="bg-muted rounded px-1">O..</code>. Neither line ever shows a cut primary text next to a cut
        tail, and a tail never outlives its content as a bare separator.
      </p>
    </div>

    {#each widthLadder as widthClass (widthClass)}
      <div class="space-y-1">
        <p class="text-muted-foreground font-mono text-xs">{widthClass}</p>
        <DashboardCard items={valueFormItems} class="bg-card h-40 rounded-lg {widthClass}" />
      </div>
    {/each}
  </section>
</div>

<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import ArgCard from "$lib/components/arg/ArgCard.svelte";
  import type { ArgData, CardData } from "$lib/components/arg/utils.svelte";
  import type { DashboardEditEvent } from "$lib/components/dashboard/dashboardEdit";
  import { cardWidth } from "$lib/use/cardWidth.svelte";
  import { elementSize } from "$lib/use/size.svelte";

  // the width of the cards follows the config pages, see cardWidth()
  let containerSize = $state({ width: 0, height: 0 });
  const cardClass = $derived(cardWidth(containerSize.width));

  // --- Mock helpers ---
  function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString().replace("Z", "");
  }

  // The generated config carries no `dt` on the `_info` row of a dashboard
  // group: the row is rendered from `_info.dashboard`
  function info(group: string, dashboard: string, color: string, name: string): any {
    return {
      group,
      arg: "_info",
      task: "Dashboard",
      dashboard,
      dashboard_color: color,
      name,
      value: "",
    };
  }
  function arg(group: string, argName: string, dt: string, value: any, extra: any = {}): any {
    return { task: "Dashboard", group, arg: argName, dt, value, ...extra };
  }
  function timeArg(group: string, msAgo: number): any {
    return arg(group, "Time", "datetime", ago(msAgo));
  }

  /**
   * One card per dashboard datatype: a card holds exactly one dashboard group,
   * named after its datatype, so the row of one type can be compared with the
   * row of another one card at a time.
   */
  type Section = {
    /** Heading of the section */
    title: string;
    /** What the type means and how its row reads */
    help: string;
    card: CardData;
  };

  /**
   * A card with a single dashboard group of the given datatype.
   *
   * @param dt Value of `_info.dashboard`, the datatype the row renders as
   * @param name Displayed name of the item, e.g. "Gems"
   * @param color Dot color of the item, see `dashboard_color`
   * @param args Args of the group, besides `_info`
   * @returns The card data of one dashboard group
   */
  function demoCard(dt: string, name: string, color: string, args: Record<string, any>): CardData {
    return {
      _info: { group: dt, arg: "_info", card: `card-Dashboard-${dt}`, name: dt } as any,
      [dt]: { _info: info(dt, dt, color, name), ...args },
    } as CardData;
  }

  // --- Non-dashboard card (normal args, for comparison) ---
  const normalCard = {
    _info: { group: "NormalDemo", arg: "_info", card: "card-Normal-Demo", name: "Non-Dashboard (normal args)" } as any,
    Settings: {
      _info: { group: "Settings", arg: "_info", name: "Settings", value: "" } as any,
      Name: arg("Settings", "Name", "input", "MyConfig"),
      Threshold: arg("Settings", "Threshold", "input-float", 0.8, { ge: 0, le: 1 }),
      Enabled: arg("Settings", "Enabled", "checkbox", true),
      Mode: arg("Settings", "Mode", "select", "auto", { option: ["auto", "manual"] }),
    },
    Output: {
      _info: { group: "Output", arg: "_info", name: "Output", value: "" } as any,
      Path: arg("Output", "Path", "input", "/data/output"),
      Retries: arg("Output", "Retries", "input-int", 3, { ge: 1, le: 10 }),
      Notes: arg("Output", "Notes", "textarea", "Optional notes here"),
    },
  } as CardData;

  // --- Mixed card (dashboard + non-dashboard groups side by side) ---
  const mixedCard = {
    _info: { group: "MixedDemo", arg: "_info", card: "card-Mixed-Demo", name: "Mixed: Dashboard + Normal" } as any,
    // Dashboard groups
    Gems: {
      _info: info("Gems", "Amount", "#eb8efe", "Gems"),
      Value: arg("Gems", "Value", "input-int", 8654),
      Time: timeArg("Gems", 5 * 60 * 1000),
    },
    Oil: {
      _info: info("Oil", "Total", "#7f7f7f", "Oil"),
      Value: arg("Oil", "Value", "input-int", 1234, { ge: 0, le: 25000 }),
      Time: timeArg("Oil", 5 * 60 * 1000),
    },
    // Normal groups (interspersed)
    Config: {
      _info: { group: "Config", arg: "_info", name: "Config", value: "" } as any,
      Threshold: arg("Config", "Threshold", "input-float", 0.85, { ge: 0, le: 1 }),
      Retries: arg("Config", "Retries", "input-int", 5, { ge: 1, le: 20 }),
    },
    Campaign: {
      _info: info("Campaign", "Progress", "#1e88e5", "Campaign"),
      Value: arg("Campaign", "Value", "input-float", 45.67, { ge: 0, le: 100 }),
      Time: timeArg("Campaign", 30 * 1000),
    },
    Research: {
      _info: info("Research", "Planner", "#8e24aa", "Research"),
      Progress: arg("Research", "Progress", "input-int", 80),
      Eta: arg("Research", "Eta", "input", "01:23:45"),
      GreenValue: arg("Research", "GreenValue", "input-int", 33, { ge: 0 }),
      GreenTotal: arg("Research", "GreenTotal", "input-int", 33, { ge: 0 }),
      BlueValue: arg("Research", "BlueValue", "input-int", 22, { ge: 0 }),
      BlueTotal: arg("Research", "BlueTotal", "input-int", 44, { ge: 0 }),
      Time: timeArg("Research", 10 * 1000),
    },
    Advanced: {
      _info: { group: "Advanced", arg: "_info", name: "Advanced", value: "" } as any,
      MaxDepth: arg("Advanced", "MaxDepth", "input-int", 3, { ge: 0, le: 10 }),
      Debug: arg("Advanced", "Debug", "checkbox", false),
    },
  } as CardData;

  // --- Dashboard datatypes, one card each ---
  // CardData declares an index signature for the groups, so object literals
  // with a typed `_info` are not assignable to it directly; the other debug
  // pages cast for the same reason (see arginput / configdisplay).
  const sections = $state<Section[]>([
    {
      title: "Amount",
      help: "DashboardAmount: one number, the row reads the value only. The edit button right of it opens the whole record: Value and the Time it was recorded at.",
      card: demoCard("Amount", "Gems", "#eb8efe", {
        Value: arg("Amount", "Value", "input-int", 8654, { ge: 0 }),
        Time: timeArg("Amount", 5 * 60 * 1000),
      }),
    },
    {
      title: "Total",
      help: "DashboardTotal: the value over a fixed total. The denominator is the `le` bound of Value, the row reads 1234 / 25000.",
      card: demoCard("Total", "Oil", "#7f7f7f", {
        Value: arg("Total", "Value", "input-int", 1234, { ge: 0, le: 25000 }),
        Time: timeArg("Total", 5 * 60 * 1000),
      }),
    },
    {
      title: "Remain",
      help: "DashboardRemain: the runs left today, the same row shape as Total, here 2 / 3.",
      card: demoCard("Remain", "HardCampaign", "#4caf50", {
        Value: arg("Remain", "Value", "input-int", 2, { ge: 0, le: 3 }),
        Time: timeArg("Remain", 15 * 60 * 1000),
      }),
    },
    {
      title: "DynamicTotal",
      help: "DashboardDynamicTotal: the value over a Total the script writes at runtime, so the denominator is the Total arg and not a bound. The dialog edits both halves.",
      card: demoCard("DynamicTotal", "SimulatedUniverse", "#ff9800", {
        Value: arg("DynamicTotal", "Value", "input-int", 5000, { ge: 0 }),
        Total: arg("DynamicTotal", "Total", "input-int", 40000, { ge: 0 }),
        Time: timeArg("DynamicTotal", 30 * 60 * 1000),
      }),
    },
    {
      title: "SoftTotal",
      help: "DashboardSoftTotal: the value over a soft Total the value may pass, while neither half may pass the hard limit of Value.le (25000). The dialog edits both halves.",
      card: demoCard("SoftTotal", "WeeklyPoints", "#00acc1", {
        Value: arg("SoftTotal", "Value", "input-int", 12345, { ge: 0, le: 25000 }),
        Total: arg("SoftTotal", "Total", "input-int", 23145, { ge: 0, le: 25000 }),
        Time: timeArg("SoftTotal", 30 * 60 * 1000),
      }),
    },
    {
      title: "Progress",
      help: "DashboardProgress: a percentage bounded by 0~100, the limit is the arg constraint itself. The row still clamps a value out of range to 0-100 when one reaches it (150 reads 100.00%).",
      card: demoCard("Progress", "Campaign", "#1e88e5", {
        Value: arg("Progress", "Value", "input-float", 45.67, { ge: 0, le: 100 }),
        Time: timeArg("Progress", 10 * 1000),
      }),
    },
    {
      title: "Planner",
      help: "DashboardPlanner: the Progress and Eta of a plan, plus its complete Value/Total pairs (8 / 10 and 22 / 44 here). Every arg of the group is editable in the dialog, the plan numbers and the pairs included.",
      card: demoCard("Planner", "Research", "#8e24aa", {
        Progress: arg("Planner", "Progress", "input-int", 80, { ge: 0, le: 100 }),
        Eta: arg("Planner", "Eta", "input", "01:23:45"),
        GreenValue: arg("Planner", "GreenValue", "input-int", 8, { ge: 0 }),
        GreenTotal: arg("Planner", "GreenTotal", "input-int", 10, { ge: 0 }),
        BlueValue: arg("Planner", "BlueValue", "input-int", 22, { ge: 0 }),
        BlueTotal: arg("Planner", "BlueTotal", "input-int", 44, { ge: 0 }),
        // A setting of the group that is not a plan number: the dialog shows it
        // like any other editable arg, the row above only summarizes the plan
        Custom: arg("Planner", "Custom", "input", "hidden"),
        Time: timeArg("Planner", 30 * 1000),
      }),
    },
    // The composition demos do not follow the one-type-per-card rule: they show
    // how dashboard and normal groups share a card
    {
      title: "Non-Dashboard (normal args)",
      help: "Groups without `_info.dashboard`: every arg renders as its own row instead of one compact dashboard row.",
      card: normalCard,
    },
    {
      title: "Mixed: Dashboard + Normal Groups",
      help: "Dashboard and normal groups interleaved in the same card, as they appear in the real config pages.",
      card: mixedCard,
    },
  ]);

  // --- Edit handlers ---
  /**
   * There is no backend on a dev page, so a save from the record editor is
   * applied to the mock cards directly: the events carry (task, group, arg),
   * the same reference the real page sends, which is enough to find the row.
   * A group name shared by two demo cards (the mixed card repeats a few of
   * them) is updated in both, they are the same mock group shown twice.
   *
   * The save toast of a config page belongs to that page (it fires on the rpc
   * success), so this page does not fake one: the dialog is the feature shown
   * here, and its own feedback (the discarded edits of a cancel) still runs.
   *
   * @param events Arg edits of one save
   */
  function applyEvents(events: DashboardEditEvent[]) {
    for (const event of events) {
      if (event.group === "_info") continue;
      for (const section of sections) {
        const group = section.card[event.group];
        if (!group) continue;
        const row = group[event.arg];
        if (!row) continue;
        row.value = event.value;
      }
    }
  }

  function handleMultiEdit(events: DashboardEditEvent[]) {
    applyEvents(events);
  }

  // The rows of a dashboard group edit their arg directly (select, checkbox,
  // input); a caller without a batch save falls back to one event per arg
  function handleEdit(data: ArgData) {
    applyEvents([{ task: data.task, group: data.group, arg: data.arg, value: data.value }]);
  }

  const noopReset = () => {};
</script>

<div class="container mx-auto flex flex-col gap-8 p-6 pb-20" use:elementSize={containerSize}>
  <h1 class="text-3xl font-bold">Dashboard Group Types</h1>
  <p class="text-muted-foreground">
    Each dashboard datatype has a card of its own, holding exactly one group with
    <code class="rounded bg-muted px-1">_info.dashboard</code>, so the row of one type can be compared with another one
    card at a time. Every dashboard group carries one summary row; the edit button right of its value opens a dialog
    with every editable arg of the group, saved as one batch.
  </p>

  {#each sections as section, index (section.title)}
    <section class="space-y-3">
      <h2 class="text-xl font-semibold">{section.title}</h2>
      <p class="text-sm text-muted-foreground">{section.help}</p>
      <ArgCard
        bind:cardData={sections[index].card}
        parentWidth={containerSize.width}
        {handleEdit}
        handleReset={noopReset}
        {handleMultiEdit}
        class={cardClass}
      />
    </section>
  {/each}
</div>

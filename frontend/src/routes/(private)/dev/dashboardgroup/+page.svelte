<script lang="ts">
  import ArgCard from "$lib/components/arg/ArgCard.svelte";
  import type { CardData } from "$lib/components/arg/utils.svelte";

  // --- Mock helpers ---
  function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString().replace("Z", "");
  }

  const noop = () => {};

  // Build a reusable arg template
  function info(group: string, dashboard: string, color: string, name: string): any {
    return {
      group, arg: "_info", task: "Dashboard", dt: "dashboard-value",
      dashboard, dashboard_color: color, name, value: "",
    };
  }
  function arg(group: string, argName: string, dt: string, value: any, extra: any = {}): any {
    return { task: "Dashboard", group, arg: argName, dt, value, ...extra };
  }
  function timeArg(group: string, msAgo: number): any {
    return arg(group, "Time", "datetime", ago(msAgo));
  }

  // --- Dashboard types as a single card (simulating real config) ---
  let dashboardCard = $state({
    _info: { group: "DashboardDemo", arg: "_info", card: "card-Dashboard-Demo", name: "Dashboard Types" },
    Amount: {
      _info: info("Amount", "Amount", "#eb8efe", "Gems"),
      Value: arg("Amount", "Value", "input-int", 8654),
      Time: timeArg("Amount", 5 * 60 * 1000),
    },
    Total: {
      _info: info("Total", "Total", "#7f7f7f", "Oil"),
      Value: arg("Total", "Value", "input-int", 1234, { ge: 0, le: 25000 }),
      Time: timeArg("Total", 5 * 60 * 1000),
    },
    Remain: {
      _info: info("Remain", "Remain", "#4caf50", "Fuel"),
      Value: arg("Remain", "Value", "input-int", 3200, { ge: 0, le: 5000 }),
      Time: timeArg("Remain", 15 * 60 * 1000),
    },
    DynamicTotal: {
      _info: info("DynamicTotal", "DynamicTotal", "#ff9800", "Dorm Food"),
      Value: arg("DynamicTotal", "Value", "input-int", 5000),
      Total: arg("DynamicTotal", "Total", "static", 40000),
      Time: timeArg("DynamicTotal", 30 * 60 * 1000),
    },
    Progress: {
      _info: info("Progress", "Progress", "#1e88e5", "Campaign"),
      Value: arg("Progress", "Value", "input-float", 45.67),
      Time: timeArg("Progress", 10 * 1000),
    },
    Overflow: {
      _info: info("Overflow", "Progress", "#e53935", "Overflow (150%)"),
      Value: arg("Overflow", "Value", "input-float", 150),
      Time: timeArg("Overflow", 2 * 60 * 1000),
    },
    Planner: {
      _info: info("Planner", "Planner", "#8e24aa", "Research"),
      Progress: arg("Planner", "Progress", "input-int", 80),
      Eta: arg("Planner", "Eta", "input", "01:23:45"),
      Time: timeArg("Planner", 30 * 1000),
    },
    PlannerDone: {
      _info: info("PlannerDone", "Planner", "#2196f3", "Commission"),
      Progress: arg("PlannerDone", "Progress", "input-int", 100),
      Eta: arg("PlannerDone", "Eta", "input", "00:00:00"),
      Time: timeArg("PlannerDone", 5 * 1000),
    },
  });

  // --- Non-dashboard card (normal args, for comparison) ---
  let normalCard = $state({
    _info: { group: "NormalDemo", arg: "_info", card: "card-Normal-Demo", name: "Non-Dashboard (normal args)" },
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
  });

  // --- Mixed card (dashboard + non-dashboard groups side by side) ---
  let mixedCard = $state({
    _info: { group: "MixedDemo", arg: "_info", card: "card-Mixed-Demo", name: "Mixed: Dashboard + Normal" },
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
      Value: arg("Campaign", "Value", "input-float", 45.67),
      Time: timeArg("Campaign", 30 * 1000),
    },
    Research: {
      _info: info("Research", "Planner", "#8e24aa", "Research"),
      Progress: arg("Research", "Progress", "input-int", 80),
      Eta: arg("Research", "Eta", "input", "01:23:45"),
      Time: timeArg("Research", 10 * 1000),
    },
    Advanced: {
      _info: { group: "Advanced", arg: "_info", name: "Advanced", value: "" } as any,
      MaxDepth: arg("Advanced", "MaxDepth", "input-int", 3, { ge: 0, le: 10 }),
      Debug: arg("Advanced", "Debug", "checkbox", false),
    },
  });

  // Full-width card class matching ArgCardList logic
  const noopEdit = noop;
  const noopReset = noop;
</script>

<div class="container mx-auto flex flex-col gap-8 overflow-auto p-6 pb-20">
  <h1 class="text-3xl font-bold">Dashboard Group Types</h1>
  <p class="text-muted-foreground">
    Each group with <code class="bg-muted rounded px-1">_info.dashboard</code> is rendered as a
    single compact arg row. Compare with normal (non-dashboard) groups below.
  </p>

  <!-- Dashboard-only card -->
  <section class="space-y-3">
    <h2 class="text-xl font-semibold">All Dashboard Types</h2>
    <p class="text-muted-foreground text-sm">
      Amount, Total, Remain, DynamicTotal, Progress, and Planner types &mdash;
      each rendered as a single arg row with PrettyValue on the right and time below.
    </p>
    <ArgCard
      bind:cardData={dashboardCard}
      parentWidth={1800}
      handleEdit={noopEdit}
      handleReset={noopReset}
      class="max-w-240"
    />
  </section>

  <!-- Non-dashboard comparison -->
  <section class="space-y-3">
    <h2 class="text-xl font-semibold">Non-Dashboard (normal args)</h2>
    <p class="text-muted-foreground text-sm">
      Groups without <code class="bg-muted rounded px-1">_info.dashboard</code> &mdash;
      each arg renders individually as separate rows.
    </p>
    <ArgCard
      bind:cardData={normalCard}
      parentWidth={1800}
      handleEdit={noopEdit}
      handleReset={noopReset}
      class="max-w-240"
    />
  </section>

  <!-- Mixed card -->
  <section class="space-y-3">
    <h2 class="text-xl font-semibold">Mixed: Dashboard + Normal Groups</h2>
    <p class="text-muted-foreground text-sm">
      Dashboard and normal groups interleaved in the same card, as they would appear
      in the real config overview.
    </p>
    <ArgCard
      bind:cardData={mixedCard}
      parentWidth={1800}
      handleEdit={noopEdit}
      handleReset={noopReset}
      class="max-w-240"
    />
  </section>
</div>

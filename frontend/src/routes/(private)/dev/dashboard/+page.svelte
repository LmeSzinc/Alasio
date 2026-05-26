<script lang="ts">
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import DashboardCard from "$lib/components/dashboard/DashboardCard.svelte";

  // Mock data based on ExampleMod/module/config/dashboard/dashboard_config.json
  // Format: groupName -> argName -> ArgData
  const baseItems: Record<string, Record<string, ArgData>> = {
    Oil: {
      _info: {
        group: "Oil", arg: "_info",
        task: "Dashboard", dt: "dashboard-total",
        dashboard: "Total", dashboard_color: "#7f7f7f",
        name: "Oil",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Oil", arg: "Value",
        dt: "input-int", value: 1234, ge: 0, le: 25000,
      },
      Time: {
        task: "Dashboard", group: "Oil", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 5 * 60 * 1000).toISOString().replace("Z", ""),
      },
    },
    Gems: {
      _info: {
        group: "Gems", arg: "_info",
        task: "Dashboard", dt: "dashboard-value",
        dashboard: "Amount", dashboard_color: "#eb8efe",
        name: "Gems",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Gems", arg: "Value",
        dt: "input-int", value: 500, ge: 0,
      },
      Time: {
        task: "Dashboard", group: "Gems", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString().replace("Z", ""),
      },
    },
    Progress: {
      _info: {
        group: "Progress", arg: "_info",
        task: "Dashboard", dt: "dashboard-progress",
        dashboard: "Progress",
        name: "Campaign",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Progress", arg: "Value",
        dt: "input-float", value: 45.67,
      },
      Time: {
        task: "Dashboard", group: "Progress", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 10 * 1000).toISOString().replace("Z", ""),
      },
    },
    Planner: {
      _info: {
        group: "Planner", arg: "_info",
        task: "Dashboard", dt: "dashboard-planner",
        dashboard: "Planner",
        name: "Research",
        value: "",
      },
      Progress: {
        task: "Dashboard", group: "Planner", arg: "Progress",
        dt: "input-int", value: 80,
      },
      Eta: {
        task: "Dashboard", group: "Planner", arg: "Eta",
        dt: "input", value: "01:23:45",
      },
      Time: {
        task: "Dashboard", group: "Planner", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 30 * 1000).toISOString().replace("Z", ""),
      },
    },
    // Additional items to test expansion (simple Amount type)
    Coins: {
      _info: {
        group: "Coins", arg: "_info",
        task: "Dashboard", dt: "dashboard-value",
        dashboard: "Amount", dashboard_color: "#ffd700",
        name: "Coins",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Coins", arg: "Value",
        dt: "input-int", value: 154321,
      },
      Time: {
        task: "Dashboard", group: "Coins", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 15 * 60 * 1000).toISOString(),
      },
    },
    Exp: {
      _info: {
        group: "Exp", arg: "_info",
        task: "Dashboard", dt: "dashboard-value",
        dashboard: "Amount", dashboard_color: "#4caf50",
        name: "Exp",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Exp", arg: "Value",
        dt: "input-int", value: 9999,
      },
      Time: {
        task: "Dashboard", group: "Exp", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
      },
    },
    Medals: {
      _info: {
        group: "Medals", arg: "_info",
        task: "Dashboard", dt: "dashboard-value",
        dashboard: "Amount", dashboard_color: "#f44336",
        name: "Medals",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Medals", arg: "Value",
        dt: "input-int", value: 42,
      },
      Time: {
        task: "Dashboard", group: "Medals", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
      },
    },
    Dorm: {
      _info: {
        group: "Dorm", arg: "_info",
        task: "Dashboard", dt: "dashboard-total",
        dashboard: "DynamicTotal", dashboard_color: "#ff9800",
        name: "Dorm Food",
        value: "",
      },
      Value: {
        task: "Dashboard", group: "Dorm", arg: "Value",
        dt: "input-int", value: 5000,
      },
      Total: {
        task: "Dashboard", group: "Dorm", arg: "Total",
        dt: "static", value: 40000,
      },
      Time: {
        task: "Dashboard", group: "Dorm", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
      },
    },
    Commission: {
      _info: {
        group: "Commission", arg: "_info",
        task: "Dashboard", dt: "dashboard-planner",
        dashboard: "Planner", dashboard_color: "#2196f3",
        name: "Commission",
        value: "",
      },
      Progress: {
        task: "Dashboard", group: "Commission", arg: "Progress",
        dt: "input-int", value: 50,
      },
      Eta: {
        task: "Dashboard", group: "Commission", arg: "Eta",
        dt: "input", value: "02:15:00",
      },
      Time: {
        task: "Dashboard", group: "Commission", arg: "Time",
        dt: "datetime", value: new Date(Date.now() - 5 * 1000).toISOString(),
      },
    },
  };

  const mockItems: Record<string, Record<string, Record<string, ArgData>>> = {
    Default: baseItems,
  };

  const mockItemsLong: Record<string, Record<string, Record<string, ArgData>>> = {
    Group1: baseItems,
    Group2: baseItems,
    Group3: baseItems,
  };
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4 pb-20">
  <h1 class="text-3xl font-bold">Dashboard Component Debug Page</h1>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Dashboard Overview</h2>
      <p class="text-muted-foreground text-sm">
        Testing the Dashboard component with mock data. It should show the first group by default and be expandable.
      </p>
    </div>

    <DashboardCard items={mockItems} />
  </section>

  <section class="space-y-4">
    <div class="space-y-1">
      <h2 class="text-xl font-semibold">Contextual Test</h2>
      <p class="text-muted-foreground text-sm">
        Demonstrating how the dashboard sits above other content when expanded.
      </p>
    </div>

    <div class="space-y-4">
      <DashboardCard items={mockItemsLong} />

      <div class="grid grid-cols-2 gap-4">
        <div class="bg-muted flex h-64 items-center justify-center rounded-xl font-mono text-sm">
          Content A (should be covered)
        </div>
        <div class="bg-muted flex h-64 items-center justify-center rounded-xl font-mono text-sm">
          Content B (should be covered)
        </div>
      </div>
    </div>
  </section>
</div>

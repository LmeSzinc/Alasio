<script lang="ts">
  import DashboardCard from "$lib/components/dashboard/DashboardCard.svelte";
  import type { DashboardArgData } from "$lib/components/dashboard/utils";

  // Mock data based on ExampleMod/module/config/dashboard/dashboard_config.json
  const baseItems: Record<string, DashboardArgData> = {
    Oil: {
      task: "Dashboard",
      group: "Oil",
      arg: "_info",
      dt: "dashboard-total",
      dashboard_color: "#7f7f7f",
      name: "Oil",
      value: {
        Value: {
          task: "Dashboard",
          group: "Oil",
          arg: "Value",
          dt: "input-int",
          value: 1234,
        },
        Total: {
          task: "Dashboard",
          group: "Oil",
          arg: "Total",
          dt: "static",
          value: 25000,
        },
        Time: {
          task: "Dashboard",
          group: "Oil",
          arg: "Time",
          dt: "datetime",
          value: new Date(Date.now() - 5 * 60 * 1000).toISOString().replace("Z", ""), // 5 min ago
        },
      },
    } as any,
    Gems: {
      task: "Dashboard",
      group: "Gems",
      arg: "_info",
      dt: "dashboard-value",
      dashboard_color: "#eb8efe",
      name: "Gems",
      value: {
        Value: {
          task: "Dashboard",
          group: "Gems",
          arg: "Value",
          dt: "input-int",
          value: 500,
        },
        Time: {
          task: "Dashboard",
          group: "Gems",
          arg: "Time",
          dt: "datetime",
          value: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString().replace("Z", ""), // 2 hours ago
        },
      },
    } as any,
    Progress: {
      task: "Dashboard",
      group: "Progress",
      arg: "_info",
      dt: "dashboard-progress",
      name: "Campaign",
      value: {
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
          value: new Date(Date.now() - 10 * 1000).toISOString().replace("Z", ""), // 10s ago
        },
      },
    } as any,
    Planner: {
      task: "Dashboard",
      group: "Planner",
      arg: "_info",
      dt: "dashboard-planner",
      name: "Research",
      value: {
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
          value: new Date(Date.now() - 30 * 1000).toISOString().replace("Z", ""), // 30s ago
        },
      },
    } as any,
    // Add more items to test expansion
    Coins: {
      task: "Dashboard",
      group: "Coins",
      arg: "_info",
      dt: "dashboard-value",
      name: "Coins",
      dashboard_color: "#ffd700",
      value: {
        Value: { value: 154321 },
        Time: { value: new Date(Date.now() - 15 * 60 * 1000).toISOString() },
      },
    } as any,
    Exp: {
      task: "Dashboard",
      group: "Exp",
      arg: "_info",
      dt: "dashboard-value",
      name: "Exp",
      dashboard_color: "#4caf50",
      value: {
        Value: { value: 9999 },
        Time: { value: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString() },
      },
    } as any,
    Medals: {
      task: "Dashboard",
      group: "Medals",
      arg: "_info",
      dt: "dashboard-value",
      name: "Medals",
      dashboard_color: "#f44336",
      value: {
        Value: { value: 42 },
        Time: { value: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString() },
      },
    } as any,
    Dorm: {
      task: "Dashboard",
      group: "Dorm",
      arg: "_info",
      dt: "dashboard-total",
      name: "Dorm Food",
      dashboard_color: "#ff9800",
      value: {
        Value: { value: 5000 },
        Total: { value: 40000 },
        Time: { value: new Date(Date.now() - 30 * 60 * 1000).toISOString() },
      },
    } as any,
    Commission: {
      task: "Dashboard",
      group: "Commission",
      arg: "_info",
      dt: "dashboard-planner",
      name: "Commission",
      dashboard_color: "#2196f3",
      value: {
        Progress: { value: 50 },
        Eta: { value: "02:15:00" },
        Time: { value: new Date(Date.now() - 5 * 1000).toISOString() },
      },
    } as any,
  };

  const mockItems: Record<string, Record<string, DashboardArgData>> = {
    Default: baseItems,
  };

  const mockItemsLong: Record<string, Record<string, DashboardArgData>> = {
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

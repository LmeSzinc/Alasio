<script lang="ts">
  import Arg from "$lib/components/arg/Arg.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import type { WORKER_STATUS } from "$lib/components/aside/types";
  import Scheduler from "$lib/components/scheduler/Scheduler.svelte";
  import * as Card from "$lib/components/ui/card";

  // All available status options
  const ALL_STATUSES: WORKER_STATUS[] = [
    "idle",
    "starting",
    "running",
    "scheduler-stopping",
    "scheduler-waiting",
    "killing",
    "force-killing",
    "disconnected",
    "error",
  ];

  // Input state for selected config
  let configNameInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "config_name",
    dt: "input",
    value: "MyConfig",
    name: "Config Name",
  });

  let statusInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "status",
    dt: "select",
    value: "idle",
    name: "Status",
    option: ALL_STATUSES,
  });

  let deviceTypeInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "device_type",
    dt: "input",
    value: "Emulator",
    name: "Device Type",
  });

  let deviceSerialInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "device_serial",
    dt: "input",
    value: "127.0.0.1:5555",
    name: "Device Serial",
  });

  let taskListInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "task_list",
    dt: "select",
    value: "empty",
    name: "Task List",
    option: ["empty", "running-only", "next-only", "running-and-next", "long-names"],
  });

  // Generate task list based on input
  const taskRunning = $derived.by(() => {
    if (taskListInput.value === "running-only" || taskListInput.value === "running-and-next") {
      return { TaskName: "CurrentTask", NextRun: Math.floor(Date.now() / 1000) };
    }
    if (taskListInput.value === "long-names") {
      return {
        TaskName: "VeryLongTaskNameThatShouldBeTruncatedInTheUI",
        NextRun: Math.floor(Date.now() / 1000),
      };
    }
    return undefined;
  });

  const taskNext = $derived.by(() => {
    if (taskListInput.value === "next-only" || taskListInput.value === "running-and-next") {
      return [
        { TaskName: "Task1", NextRun: Math.floor(Date.now() / 1000) + 3600 },
        { TaskName: "Task2", NextRun: Math.floor(Date.now() / 1000) + 7200 },
        { TaskName: "Task3", NextRun: Math.floor(Date.now() / 1000) + 100000 },
      ];
    }
    if (taskListInput.value === "long-names") {
      return [
        {
          TaskName: "AnotherVeryLongTaskNameThatWillDefinitelyBeTruncated",
          NextRun: Math.floor(Date.now() / 1000) + 1800,
        },
        {
          TaskName: "ShortTask",
          NextRun: Math.floor(Date.now() / 1000) + 3600,
        },
      ];
    }
    return [];
  });
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <h1 class="text-3xl font-bold">Scheduler Component Debug Page</h1>

  <div class="grid gap-4 md:grid-cols-2">
    <!-- Selected Config Preview -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Selected Scheduler Preview</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="flex items-start gap-4">
          <div class="flex-1">
            <Scheduler
              config_name={configNameInput.value as string}
              status={statusInput.value as WORKER_STATUS}
              deviceType={deviceTypeInput.value as string}
              deviceSerial={deviceSerialInput.value as string}
              {taskRunning}
              {taskNext}
              onOverviewClick={() => console.log("Overview clicked")}
              onDeviceClick={() => console.log("Device clicked")}
            />
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Control Panel -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Control Panel</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="space-y-3">
          <Arg bind:data={configNameInput} />
          <Arg bind:data={statusInput} />
          <Arg bind:data={deviceTypeInput} />
          <Arg bind:data={deviceSerialInput} />
          <Arg bind:data={taskListInput} />
        </div>
      </Card.Content>
    </Card.Root>
  </div>

  <!-- All Status Combinations -->
  <div class="flex flex-col gap-4">
    <!-- Normal task list -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Normal Task List (Running + Next)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATUSES as status}
            <Scheduler
              config_name="TestConfig"
              {status}
              deviceType="Emulator"
              deviceSerial="127.0.0.1:5555"
              taskRunning={{ TaskName: "CurrentTask", NextRun: Math.floor(Date.now() / 1000) }}
              taskNext={[
                { TaskName: "NextTask1", NextRun: Math.floor(Date.now() / 1000) + 3600 },
                { TaskName: "NextTask2", NextRun: Math.floor(Date.now() / 1000) + 7200 },
              ]}
            />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Empty task list -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Empty Task List</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATUSES as status}
            <Scheduler config_name="EmptyScheduler" {status} taskNext={[]} />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Long names -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Long Config and Task Names</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATUSES as status}
            <Scheduler
              config_name="VeryLongConfigurationNameThatShouldBeTruncatedInTheUserInterface"
              {status}
              deviceType="VeryLongEmulatorType"
              deviceSerial="127.0.0.1:5555-with-very-long-serial-number"
              taskRunning={{
                TaskName: "VeryLongTaskNameThatWillBeTruncated",
                NextRun: Math.floor(Date.now() / 1000),
              }}
              taskNext={[
                {
                  TaskName: "AnotherVeryLongTaskNameForTesting",
                  NextRun: Math.floor(Date.now() / 1000) + 1800,
                },
              ]}
            />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Only next tasks -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Next Tasks Only (No Running Task)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATUSES as status}
            <Scheduler
              config_name="NextOnly"
              {status}
              deviceType="Device"
              deviceSerial="123456"
              taskNext={[
                { TaskName: "Future1", NextRun: Math.floor(Date.now() / 1000) + 600 },
                { TaskName: "Future2", NextRun: Math.floor(Date.now() / 1000) + 3600 },
                { TaskName: "Future3", NextRun: Math.floor(Date.now() / 1000) + 100000 },
              ]}
            />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- No device info -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>No Device Info</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATUSES as status}
            <Scheduler
              config_name="NoDevice"
              {status}
              taskRunning={{ TaskName: "Task", NextRun: Math.floor(Date.now() / 1000) }}
              taskNext={[{ TaskName: "Next", NextRun: Math.floor(Date.now() / 1000) + 3600 }]}
            />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>
  </div>
</div>

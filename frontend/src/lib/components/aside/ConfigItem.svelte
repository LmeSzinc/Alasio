<script lang="ts">
  import { cn } from "$lib/utils.js";
  import ConfigStatus from "./ConfigStatus.svelte";
  import ModIcon from "./ModIcon.svelte";
  import { useWorkerStatus } from "./status.svelte";
  import type { ConfigLike, WORKER_STATUS } from "./types";

  // props
  type Props<T extends ConfigLike = ConfigLike> = {
    config: T;
    status?: WORKER_STATUS;
    active?: boolean;
    class?: string;
    onclick?: (config: T) => void;
    afspin?: boolean;
  };
  let { config, status = "idle", active = false, class: className, onclick, afspin = false }: Props = $props();

  const displayStatus = useWorkerStatus(() => status);
  const RUNNING_STATUSES: WORKER_STATUS[] = ["running", "scheduler-stopping", "scheduler-waiting"];
  const spin = $derived(afspin && RUNNING_STATUSES.includes(displayStatus.value));

  // callbacks
  function handleClick() {
    onclick?.(config);
  }
</script>

<button
  class={cn(
    "focus:ring-ring flex w-16 cursor-pointer flex-col items-center rounded-md py-1.5 transition-colors",
    active
      ? "bg-primary hover:bg-primary text-primary-foreground/85"
      : "hover:bg-accent hover:text-primary text-foreground/70",
    className,
  )}
  onclick={handleClick}
  disabled={!onclick}
  aria-label="Open configuration: {config.name}"
  title={config.name}
>
  <div class="relative">
    <ModIcon mod={config.mod} afspin={spin} />
    <ConfigStatus status={displayStatus.value} {active} class="absolute -right-1 bottom-0" />
  </div>
  <span class="line-clamp-2 text-center text-xs break-all" aria-hidden="true">
    {config.name}
  </span>
</button>

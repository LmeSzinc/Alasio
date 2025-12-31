import { onDestroy, untrack } from "svelte";
import type { WORKER_STATUS } from "./types";

const STABLE_STATES: WORKER_STATUS[] = ["idle", "running", "scheduler-waiting", "error", "scheduler-stopping"];

export function useWorkerStatus(getStatus: () => WORKER_STATUS) {
  let displayStatus = $state<WORKER_STATUS>("idle");
  let statusTimer: number | undefined = $state(undefined);

  $effect(() => {
    const newStatus = getStatus();

    untrack(() => {
      if (statusTimer !== undefined) {
        clearTimeout(statusTimer);
        statusTimer = undefined;
      }
    });

    if (STABLE_STATES.includes(newStatus)) {
      displayStatus = newStatus;
    } else {
      statusTimer = window.setTimeout(() => {
        displayStatus = newStatus;
      }, 300);
    }
  });

  onDestroy(() => {
    if (statusTimer !== undefined) {
      clearTimeout(statusTimer);
    }
  });

  return {
    get value() {
      return displayStatus;
    },
  };
}

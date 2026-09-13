import { onDestroy, untrack } from "svelte";
import type { WORKER_STATE } from "./types";

const STABLE_STATES: WORKER_STATE[] = [
  "idle",
  "running",
  "scheduler-waiting",
  "error",
  "scheduler-stopping",
  // restart states stay for a while (restarting until the backend restarted,
  // resuming until the queue reaches the config): show them immediately
  "restarting",
  "resuming",
];

export function useWorkerState(getState: () => WORKER_STATE) {
  let displayState = $state<WORKER_STATE>("idle");
  let stateTimer: number | undefined = $state(undefined);

  $effect(() => {
    const newState = getState();

    untrack(() => {
      if (stateTimer !== undefined) {
        clearTimeout(stateTimer);
        stateTimer = undefined;
      }
    });

    if (STABLE_STATES.includes(newState)) {
      displayState = newState;
    } else {
      stateTimer = window.setTimeout(() => {
        displayState = newState;
      }, 300);
    }
  });

  onDestroy(() => {
    if (stateTimer !== undefined) {
      clearTimeout(stateTimer);
    }
  });

  return {
    get value() {
      return displayState;
    },
  };
}

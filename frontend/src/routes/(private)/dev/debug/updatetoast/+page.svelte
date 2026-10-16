<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import { onDestroy } from "svelte";
  import { toast } from "svelte-sonner";
  import { Button } from "$lib/components/ui/button";
  import {
    UPDATE_TOAST_ID_PREFIX,
    type UpdateToastData,
    dismissUpdateToast,
    showUpdateToast,
  } from "$lib/components/update/updateToast";
  import { prettyVersion } from "$lib/components/update/utils";

  /**
   * Preview of the "update available" toast, the persistent notification of
   * the mod update flow: it never auto-closes, the user closes it, and a newer
   * set of updates replaces it (the previous toast is dismissed and plays its
   * exit animation while the new one enters).
   *
   * The mock state below plays the role of the Update topic: every button
   * changes the set of available updates the way the backend would (a mod
   * becomes available, a newer version is reported), then hands the whole set
   * to the production module (`updateToast.ts`).
   */

  // Versions of the mock data: 40 hex characters are the sha1 of a packed
  // commit (shown shortened to 8), anything else is a tag / wheel version
  // (shown as it is)
  const SHA = {
    a: "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8",
    b: "ab41c81d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b",
    c: "d3599d6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e",
  } as const;

  const LONG_NAME = "ExampleMod_with_a_very_long_repository_name";
  const LONG_VERSION = "v1.2.3-rc.4+build.20261016.abcdef1234567890";

  // --- The mock Update topic: the available updates accumulate here ---
  let mockUpdates = $state<UpdateToastData[]>([]);

  /** One mod of the mock topic enters / stays in 'available' */
  function upsert(mod: string, latest: string) {
    const update = mockUpdates.find((entry) => entry.mod === mod);
    if (update) {
      update.latest = latest;
    } else {
      mockUpdates.push({ mod, latest });
    }
  }

  /** Show everything the mock topic reports available right now */
  function show() {
    showUpdateToast([...mockUpdates]);
  }

  function reset() {
    mockUpdates = [];
    dismissUpdateToast();
  }

  // --- Burst: six replacements, one every 400 ms (animation stress) ---
  let burstTimer: ReturnType<typeof setInterval> | undefined;

  function burst() {
    clearInterval(burstTimer);
    let index = 0;
    burstTimer = setInterval(() => {
      index += 1;
      if (index > 6) {
        clearInterval(burstTimer);
        burstTimer = undefined;
        return;
      }
      upsert("alasio", `v1.0.${index}`);
      show();
    }, 400);
  }

  onDestroy(() => clearInterval(burstTimer));

  type Action = {
    label: string;
    note: string;
    run: () => void;
  };

  const actions: Action[] = [
    {
      label: "Update · first mod",
      note: "alasio becomes available: the toast announces it and never auto-closes -- leave it on screen and watch that nothing dismisses it.",
      run: () => {
        upsert("alasio", SHA.a);
        show();
      },
    },
    {
      label: "Update · second mod",
      note: "ExampleMod becomes available while the toast is still up: the previous toast is dismissed (its exit animation plays) and the new one enters announcing both mods. The information accumulates, the toasts never stack.",
      run: () => {
        upsert("ExampleMod", SHA.c);
        show();
      },
    },
    {
      label: "Update · third mod (tag)",
      note: "A wheel mod joins with a tag version: the row list carries every mod with its own version, tags as they are and packed sha1 shortened to 8.",
      run: () => {
        upsert("wheel_mod", "0.9.2");
        show();
      },
    },
    {
      label: "Newer version of a mod",
      note: "alasio reports a newer latest: the replacement animation again, with the version of the row updated in place.",
      run: () => {
        upsert("alasio", SHA.b);
        show();
      },
    },
    {
      label: "Long mod name",
      note: "A single update with a long mod name: the name column stops at half of the row, so the name truncates (the full name stays in its tooltip) and the version column keeps its place.",
      run: () => {
        mockUpdates = [{ mod: LONG_NAME, latest: SHA.a }];
        show();
      },
    },
    {
      label: "Long name · long version",
      note: "Both sides are long: every column of a row stops at half of the width, so the name and the version truncate instead of squeezing each other (both tooltips carry the full values).",
      run: () => {
        mockUpdates = [{ mod: LONG_NAME, latest: LONG_VERSION }];
        show();
      },
    },
    {
      label: "Close the toast",
      note: "The programmatic twin of the close button inside the toast content: the exit animation plays and the toast leaves. Closing is not cancelling -- the mock available updates stay.",
      run: () => dismissUpdateToast(),
    },
    {
      label: "Burst · 6 replacements",
      note: "Six replacements, one every 400 ms: the replacement path stays stable under a burst (the exit of one toast and the entrance of the next keep up, nothing stacks).",
      run: burst,
    },
    {
      label: "Next to a normal toast",
      note: "An ordinary toast of the Toaster (it auto-closes, the update one never does) beside the update toast: they stack like any two toasts.",
      run: () => {
        toast.info("A normal toast, it closes by itself");
        show();
      },
    },
    {
      label: "Reset",
      note: "Clears the mock available updates (as if every mod had been updated) and takes the toast down.",
      run: reset,
    },
  ];

  // --- Live view of the Toaster: which toasts are on screen right now ---
  type LiveToast = {
    id: string | number;
    type: string;
    // "never" for the update toast (duration = Infinity), the milliseconds of
    // the toast otherwise ("default" = the Toaster duration)
    duration: string;
  };

  let liveToasts = $state<LiveToast[]>([]);

  function refreshToasts() {
    liveToasts = toast.getActiveToasts().map((entry) => ({
      id: entry.id,
      type: entry.type,
      duration: entry.duration === Number.POSITIVE_INFINITY ? "never" : String(entry.duration ?? "default"),
    }));
  }

  // The toast state is a plain module state, not a Svelte signal this page can
  // subscribe to: poll it (the list is a debug view, a beat of latency is
  // fine) instead of wrapping the whole Toaster
  $effect(() => {
    refreshToasts();
    const timer = setInterval(refreshToasts, 500);
    return () => clearInterval(timer);
  });

  const updateToastOnScreen = $derived(liveToasts.some((entry) => String(entry.id).startsWith(UPDATE_TOAST_ID_PREFIX)));
</script>

<div class="container mx-auto flex flex-col gap-8 p-4 pb-20">
  <div class="space-y-2">
    <h1 class="text-3xl font-bold">Update Toast Debug Page</h1>
    <p class="text-muted-foreground text-sm">
      Every scenario of the "update available" toast: the persistent notification of the mod update flow. It stays on
      screen until the user closes it, and a newer set of updates replaces it (the previous toast is dismissed with its
      exit animation while the new one enters -- the toasts never stack, and the newest toast always carries the whole
      set of available updates). The toast always reads as a title row plus one row per mod (the name left, its version
      right), so one update and five look alike. The buttons below drive the production module (updateToast.ts), so the
      toast previewed here is the production one, not a copy.
    </p>
  </div>

  <section class="space-y-4">
    <h2 class="text-lg font-semibold">Scenarios</h2>
    <div class="flex flex-col gap-4">
      {#each actions as action (action.label)}
        <div class="flex items-center gap-4">
          <Button variant="outline" class="w-60 justify-start" onclick={action.run}>{action.label}</Button>
          <p class="text-muted-foreground text-xs">{action.note}</p>
        </div>
      {/each}
    </div>
  </section>

  <section class="space-y-2">
    <h2 class="text-lg font-semibold">Mock state</h2>
    <p class="text-muted-foreground text-xs">
      The available updates the buttons above have reported so far, the payload the next toast would carry. The Update
      topic owns this state in production; the page only fakes it.
    </p>
    <p class="font-mono text-xs">
      {mockUpdates.length === 0
        ? "(no available update)"
        : mockUpdates.map((update) => `${update.mod} @ ${prettyVersion(update.latest)}`).join(", ")}
    </p>
  </section>

  <section class="space-y-2">
    <h2 class="text-lg font-semibold">Live toasts</h2>
    <p class="text-muted-foreground text-xs">
      The active toasts of the Toaster, polled every 500 ms. The update toast is the entry whose id starts with
      <code>update-available</code> and shows <code>duration=never</code>; a replacement keeps the list at a single
      update entry at any moment (the one leaving is already dismissed, the next one is a fresh id).
    </p>
    <p class="text-sm">
      Update toast on screen:
      <span class="font-medium">{updateToastOnScreen ? "yes" : "no"}</span>
    </p>
    {#if liveToasts.length === 0}
      <p class="text-muted-foreground font-mono text-xs">(no toast on screen)</p>
    {:else}
      <ul class="font-mono text-xs">
        {#each liveToasts as entry (entry.id)}
          <li>id={entry.id} type={entry.type} duration={entry.duration}</li>
        {/each}
      </ul>
    {/if}
  </section>
</div>

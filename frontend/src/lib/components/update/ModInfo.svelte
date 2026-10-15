<script lang="ts">
  import { untrack } from "svelte";
  import type { UpdateInfoLike, UpdateTopicLike } from "$lib/components/aside/types";
  import { readableTime } from "$lib/components/dashboard/readableTime";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
  import { globalClock } from "$lib/use/clock.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import ModCommit from "./ModCommit.svelte";
  import { type ModHistory, type ModOption, prettyVersion } from "./utils";

  // One mod of the ModManager list as a card: the title row (the mod, the
  // version of its pack, the state of its update and the check button), the
  // message of a failure below the title row and the commit history.
  //
  // The update entry comes from the prop (the list reads the Update topic) but
  // the check button calls the rpc of that topic itself, like the rest of the
  // update flow.
  type $props = {
    mod: ModOption;
    history?: ModHistory;
    info?: UpdateInfoLike;
  };
  let { mod, history, info }: $props = $props();

  // Number of commits shown before expanding
  const PREVIEW_COUNT = 3;
  // states whose check waits for a window: a start is queued, see the
  // maintenance flows of the backend. 'unmanaged', 'no-supervisor' and
  // 'disabled' are never checked by the backend (a check would be refused)
  const UPDATE_NOT_CHECKABLE = [
    "no-supervisor",
    "unmanaged",
    "disabled",
    "checking",
    "queued",
    "stopping",
    "downloading",
    "updating",
  ];

  const updateTopic = useTopic<UpdateTopicLike>("Update");
  const updateCheckRpc = updateTopic.rpc();
  const cancelCheckRpc = updateTopic.rpc();

  // the checked time of the mod is a relative time ("5m ago"): keep the clock going
  globalClock.use();

  // whether every commit of the mod is expanded
  let showAll = $state(false);

  const items = $derived(history?.data ?? []);
  const visibleItems = $derived(showAll ? items : items.slice(0, PREVIEW_COUNT));
  // the version of the mod is the one of its newest commit, or the version of
  // its pack reported by the update entry when the history is unavailable
  const version = $derived(items[0]?.version || info?.current_version || "");
  // the state of a mod the backend has no entry for yet: it waits for a check
  // (the local variable is not named `state`: `$state` would read as the store
  // subscription of it)
  const updateState = $derived(info?.state ?? "idle");

  // --- flash of the state pill ---

  // The pill flashes when the moment of the last finished check changes: the
  // check the user asked for gets its feedback, the values pushed when the page
  // opens do not (the first value observed never flashes).
  let flashing = $state(false);
  let lastCheckedAt = $state<number | undefined>(undefined);
  let flashTimer: ReturnType<typeof setTimeout> | undefined;

  $effect(() => {
    const checkedAt = info?.checked_at ?? 0;
    // the effect depends on the checked time only: the remembered value is read
    // and written untracked, so storing it never schedules the effect again
    untrack(() => {
      const previous = lastCheckedAt;
      lastCheckedAt = checkedAt;
      if (previous === undefined || previous === checkedAt) return;
      flashing = true;
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        flashing = false;
      }, 1000);
    });
  });

  $effect(() => () => clearTimeout(flashTimer));

  /**
   * Pill of the update state of the mod: the state, then the value it carries
   * ("Up to date - 5m ago", "Update available - ab41c81").
   */
  function updateLabel(info: UpdateInfoLike | undefined): string {
    switch (info?.state) {
      case "no-supervisor":
        return t.Update.StateNoSupervisor();
      case "unmanaged":
        return t.Update.StateUnmanaged();
      case "disabled":
        return t.Update.StateDisabled();
      case "checking":
        return t.Update.StateChecking();
      case "uptodate":
        // the value of a mod up to date is when the last check finished
        return info.checked_at
          ? `${t.Update.StateUptodate()} - ${t.Update.CheckedAt({
              time: readableTime(new Date(info.checked_at * 1000), globalClock.now),
            })}`
          : t.Update.StateUptodate();
      case "available":
        // the value of an available update is the version it brings
        return info.latest_version
          ? `${t.Update.StateAvailable()} - ${prettyVersion(info.latest_version)}`
          : t.Update.StateAvailable();
      case "queued":
        return t.Update.StateQueued();
      case "stopping":
        return t.Update.StateStopping();
      case "downloading":
        return t.Update.StateDownloading();
      case "updating":
        return t.Update.StateUpdating();
      case "restart-required":
        return t.Update.StateRestartRequired();
      case "error":
        return t.Update.StateError();
      default:
        // 'idle', or no entry at all: nothing checked this mod yet
        return t.Update.StateIdle();
    }
  }

  /** Color of the pill of the state (a failure reads red, an update waits in primary). */
  function stateVariant(state: string): "default" | "secondary" {
    return state === "available" ? "default" : "secondary";
  }

  /** Extra classes of the pill of the state: a failure takes a red background. */
  function stateClass(state: string): string | false {
    return state === "error" && !flashing && "bg-destructive text-white";
  }
</script>

<Card.Root class="neushadow flex flex-col gap-3 border-none">
  <!-- the title row: the mod, the version of its pack, the update state, then
       the button of the check of that mod. The message of a failed check or
       update follows the title row inside the header: it belongs to the title,
       not to the history below the gap of the card -->
  <Card.Header class="flex flex-col items-stretch gap-2">
    <div class="flex w-full items-center justify-between gap-2">
      <div class="flex min-w-0 items-center gap-2">
        <Card.Title class="truncate">{mod.label}</Card.Title>
        <!-- the version pill is always there, even when the pack of the mod is
             unknown (the layout of the title row stays the same) -->
        <Badge variant="default" class="shrink-0 bg-blue-600 font-mono" title={version}>
          {version ? prettyVersion(version) : t.Update.VersionUnavailable()}
        </Badge>
        <Badge
          variant={flashing ? "default" : stateVariant(updateState)}
          class={cn("shrink-0", stateClass(updateState))}
        >
          {updateLabel(info)}
        </Badge>
      </div>
      <span class="shrink-0">
        {#if updateState === "checking"}
          <Button
            class="bg-card"
            size="xs"
            variant="outline"
            onclick={() => cancelCheckRpc.call("cancel_check", { name: mod.value })}
          >
            {t.Update.ButtonCancel()}
          </Button>
        {:else}
          <Button
            class="bg-card"
            size="xs"
            variant="outline"
            disabled={UPDATE_NOT_CHECKABLE.includes(updateState)}
            onclick={() => updateCheckRpc.call("update_check", { name: mod.value })}
          >
            {t.Update.ButtonCheck()}
          </Button>
        {/if}
      </span>
    </div>
    {#if info?.error}
      <div class="text-destructive text-xs">{info.error}</div>
    {/if}
  </Card.Header>
  <Card.Content class="grow">
    {#if history?.error}
      <div class="text-destructive text-sm">{history.error}</div>
    {:else if items.length === 0}
      <div class="text-muted-foreground text-sm">{t.Mod.NoHistory()}</div>
    {:else}
      <div
        class="text-muted-foreground mb-1 grid grid-cols-[90px_110px_160px_minmax(0,1fr)_28px] items-center gap-x-2 border-b pb-1 text-xs"
      >
        <span>{t.Mod.Version()}</span>
        <span>{t.Mod.Author()}</span>
        <span>{t.Mod.Time()}</span>
        <span>{t.Mod.CommitTitle()}</span>
        <span></span>
      </div>
      <div>
        {#each visibleItems as item (item.version)}
          <ModCommit {item} />
        {/each}
        {#if items.length > PREVIEW_COUNT}
          <div class="flex justify-center">
            <Button variant="ghost" size="xs" onclick={() => (showAll = !showAll)}>
              {#if showAll}
                {t.Mod.CollapseAll()}
              {:else}
                {t.Mod.ExpandAll()}
              {/if}
            </Button>
          </div>
        {/if}
      </div>
    {/if}
  </Card.Content>
</Card.Root>

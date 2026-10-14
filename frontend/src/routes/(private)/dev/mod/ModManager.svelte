<script lang="ts">
  import type { UpdateInfoLike, UpdateTopicLike } from "$lib/components/aside/types";
  import * as AlertDialog from "$lib/components/ui/alert-dialog";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import ModCommit, { type HistoryItem } from "./ModCommit.svelte";

  export type ModOption = {
    value: string;
    label: string;
  };
  export type ModHistoryData = Record<string, { data?: HistoryItem[]; error?: string }>;

  type $props = {
    class?: string;
    mods?: ModOption[];
    history?: ModHistoryData;
  };
  let { class: className, mods: modsProp, history: historyProp }: $props = $props();

  // Number of commits shown before expanding
  const PREVIEW_COUNT = 3;
  const SHA1_REGEX = /^[0-9a-f]{40}$/;

  const modListTopic = useTopic<ModOption[]>("ModList");
  const modHistoryTopic = useTopic<ModHistoryData>("ModHistory");
  // update states of the update flow: the badges, the buttons and the
  // confirmation dialog read the Update topic, the buttons call its rpc.
  // The four rpcs are two symmetric pairs: update_check / cancel_check
  // address one mod, update_apply / cancel_apply the update flow
  const updateTopic = useTopic<UpdateTopicLike>("Update");
  const updateCheckRpc = updateTopic.rpc();
  const cancelCheckRpc = updateTopic.rpc();
  const updateApplyRpc = updateTopic.rpc();
  const cancelApplyRpc = updateTopic.rpc();

  // data from props for testing, otherwise from topics
  const mods = $derived(modsProp ?? modListTopic.data ?? []);
  const historyData = $derived(historyProp ?? modHistoryTopic.data);

  // per mod: whether all commits are expanded
  const showAll = $state<Record<string, boolean>>({});

  function toggleShowAll(mod: string) {
    showAll[mod] = !showAll[mod];
  }

  // --- update flow ---

  // the pending "update" confirmation dialog (true = open): the update is a
  // single entry (every mod with an available update is applied, then the
  // backend restarts), see the update flow of the backend
  let confirmUpdate = $state(false);
  // states whose update runs (or waits for a window): a start is queued,
  // see the maintenance flows of the backend
  const UPDATE_NOT_CHECKABLE = ["unmanaged", "checking", "queued", "stopping", "downloading", "updating"];
  // states of the update flow that may still be cancelled (the check of a mod,
  // the stop of the workers, the download): 'updating' is past the point of no
  // return, the flow restarts the backend by itself when it finishes
  const UPDATE_CANCELLABLE = ["checking", "queued", "stopping", "downloading"];

  function updateInfo(mod: string): UpdateInfoLike | undefined {
    return updateTopic.data?.[mod];
  }

  /** The mods with an available update (the scope of the single update entry). */
  const availableMods = $derived(mods.filter((mod) => updateInfo(mod.value)?.state === "available"));
  /** Whether the update flow of the backend may still be cancelled. */
  const updateCancellable = $derived(
    mods.some((mod) => {
      const state = updateInfo(mod.value)?.state;
      return state !== undefined && UPDATE_CANCELLABLE.includes(state);
    }),
  );

  /** Badge label of an update state, '' when the mod has no entry yet. */
  function updateLabel(info: UpdateInfoLike | undefined): string {
    switch (info?.state) {
      case "unmanaged":
        return t.Update.StateUnmanaged();
      case "idle":
        return t.Update.StateIdle();
      case "checking":
        return t.Update.StateChecking();
      case "uptodate":
        return t.Update.StateUptodate();
      case "available":
        return t.Update.StateAvailable();
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
        return "";
    }
  }

  function confirmApply() {
    confirmUpdate = false;
    updateApplyRpc.call("update_apply", {});
  }
</script>

{#if mods.length === 0}
  <div class="text-muted-foreground flex items-center justify-center rounded-lg border-2 border-dashed py-16 text-sm">
    {t.Mod.NoMod()}
  </div>
{:else}
  <div class={cn("flex flex-col gap-4", className)}>
    <!-- the single update entry: every mod with an available update is
         applied, then the backend restarts and resumes the running configs -->
    <div class="flex items-center justify-between gap-2">
      <span class="text-muted-foreground text-sm">
        {availableMods.length
          ? t.Update.AvailableCount({ count: String(availableMods.length) })
          : t.Update.NoAvailable()}
      </span>
      <span class="flex shrink-0 gap-2">
        {#if updateCancellable}
          <Button size="sm" variant="outline" onclick={() => cancelApplyRpc.call("cancel_apply", {})}>
            {t.Update.ButtonCancel()}
          </Button>
        {/if}
        <Button size="sm" disabled={availableMods.length === 0} onclick={() => (confirmUpdate = true)}>
          {t.Update.ButtonUpdate()}
        </Button>
      </span>
    </div>
    {#each mods as mod (mod.value)}
      {@const history = historyData?.[mod.value]}
      {@const items = history?.data ?? []}
      {@const visibleItems = showAll[mod.value] ? items : items.slice(0, PREVIEW_COUNT)}
      {@const version = items[0]?.version ?? ""}
      {@const info = updateInfo(mod.value)}
      <Card.Root class="flex flex-col">
        <Card.Header class="flex flex-row items-center justify-between gap-2">
          <Card.Title class="truncate">{mod.label}</Card.Title>
          {#if version}
            <Badge variant="secondary" class="shrink-0 font-mono" title={version}>
              {SHA1_REGEX.test(version) ? version.slice(0, 7) : version}
            </Badge>
          {/if}
        </Card.Header>
        <Card.Content class="grow">
          {#if info}
            <div class="mb-2 flex flex-wrap items-center gap-2 border-b pb-2 text-sm">
              <Badge variant={info.state === "available" ? "default" : "secondary"} class="shrink-0">
                {updateLabel(info)}
              </Badge>
              {#if info.current_version || info.latest_version}
                <span class="text-muted-foreground truncate font-mono text-xs">
                  {info.current_version || t.Update.VersionNone()}
                  →
                  {info.latest_version || t.Update.VersionUnknown()}
                </span>
              {/if}
              <span class="ml-auto flex shrink-0 gap-2">
                {#if info.state === "checking"}
                  <Button
                    size="sm"
                    variant="outline"
                    onclick={() => cancelCheckRpc.call("cancel_check", { name: mod.value })}
                  >
                    {t.Update.ButtonCancel()}
                  </Button>
                {:else}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={UPDATE_NOT_CHECKABLE.includes(info.state)}
                    onclick={() => updateCheckRpc.call("update_check", { name: mod.value })}
                  >
                    {t.Update.ButtonCheck()}
                  </Button>
                {/if}
              </span>
            </div>
            {#if info.error}
              <div class="text-destructive mb-2 text-xs">{info.error}</div>
            {/if}
          {/if}
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
            <div class="divide-border divide-y">
              {#each visibleItems as item (item.version)}
                <ModCommit {item} />
              {/each}
            </div>
            {#if items.length > PREVIEW_COUNT}
              <div class="mt-2 flex justify-center">
                <Button variant="outline" size="sm" onclick={() => toggleShowAll(mod.value)}>
                  {#if showAll[mod.value]}
                    {t.Mod.CollapseAll()}
                  {:else}
                    {t.Mod.ExpandAll()}
                  {/if}
                </Button>
              </div>
            {/if}
          {/if}
        </Card.Content>
      </Card.Root>
    {/each}
  </div>
{/if}

<!-- "update" confirmation: one update applies every mod with an available
     update as a full backend restart, every running config comes back after it -->
<AlertDialog.Root
  open={confirmUpdate}
  onOpenChange={(value: boolean) => {
    if (!value) confirmUpdate = false;
  }}
>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>{t.Update.ConfirmTitle()}</AlertDialog.Title>
      <AlertDialog.Description>
        {t.Update.ConfirmDescription({
          mods: availableMods.map((mod) => mod.label).join(", "),
        })}
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel>{t.Update.ButtonCancel()}</AlertDialog.Cancel>
      <Button onclick={confirmApply}>{t.Update.ButtonUpdate()}</Button>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>

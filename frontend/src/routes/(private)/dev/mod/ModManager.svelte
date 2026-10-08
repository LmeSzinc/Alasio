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
  // confirmation dialog read the Update topic, the buttons call its rpc
  const updateTopic = useTopic<UpdateTopicLike>("Update");
  const updateCheckRpc = updateTopic.rpc();
  const updateApplyRpc = updateTopic.rpc();
  const updateCancelRpc = updateTopic.rpc();

  // data from props for testing, otherwise from topics
  const mods = $derived(modsProp ?? modListTopic.data ?? []);
  const historyData = $derived(historyProp ?? modHistoryTopic.data);

  // per mod: whether all commits are expanded
  const showAll = $state<Record<string, boolean>>({});

  function toggleShowAll(mod: string) {
    showAll[mod] = !showAll[mod];
  }

  // --- update flow ---

  // the mod of the pending "update" confirmation dialog (null = closed)
  let confirmMod = $state<ModOption | null>(null);
  // states whose update runs (or waits for a window): a start is queued,
  // see the update flow of the backend
  const UPDATE_NOT_CHECKABLE = ["unmanaged", "checking", "downloading", "updating"];

  function updateInfo(mod: string): UpdateInfoLike | undefined {
    return updateTopic.data?.[mod];
  }

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
      case "downloading":
        return t.Update.StateDownloading();
      case "updating":
        return t.Update.StateUpdating();
      case "error":
        return t.Update.StateError();
      default:
        return "";
    }
  }

  function confirmUpdate() {
    const mod = confirmMod;
    confirmMod = null;
    if (mod) updateApplyRpc.call("update_apply", { name: mod.value });
  }
</script>

{#if mods.length === 0}
  <div class="text-muted-foreground flex items-center justify-center rounded-lg border-2 border-dashed py-16 text-sm">
    {t.Mod.NoMod()}
  </div>
{:else}
  <div class={cn("flex flex-col gap-4", className)}>
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
                {#if info.state === "available"}
                  <Button size="sm" onclick={() => (confirmMod = mod)}>
                    {t.Update.ButtonUpdate()}
                  </Button>
                {:else if info.state === "checking" || info.state === "downloading"}
                  <Button size="sm" variant="outline" onclick={() => updateCancelRpc.call("update_cancel")}>
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

<!-- "update" confirmation: v1 applies any mod update as a full backend
     restart, every running config comes back after it -->
<AlertDialog.Root
  open={confirmMod !== null}
  onOpenChange={(value: boolean) => {
    if (!value) confirmMod = null;
  }}
>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>{t.Update.ConfirmTitle()}</AlertDialog.Title>
      <AlertDialog.Description>
        {t.Update.ConfirmDescription({ mod: confirmMod?.label ?? "" })}
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel>{t.Update.ButtonCancel()}</AlertDialog.Cancel>
      <Button onclick={confirmUpdate}>{t.Update.ButtonUpdate()}</Button>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>

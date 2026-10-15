<script lang="ts">
  import type { UpdateTopicLike } from "$lib/components/aside/types";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { type ModOption } from "./utils";

  // The control row of the whole update flow: the button that applies every
  // available update, the button that cancels a flow that may still be
  // cancelled, and the state of the updates.
  //
  // The row derives everything from the mods and their update states, and owns
  // the two rpcs of the flow: update_apply applies every available update at
  // once (then the backend restarts and resumes the running configs),
  // cancel_apply stops the flow while it may still be cancelled.
  //
  // There is no confirmation dialog here: the user reaches this page from the
  // popup that announces the update, so clicking the button is already the
  // second confirmation. The two callbacks let a caller take the buttons over
  // instead (the tests drive the row with them).
  type $props = {
    /** The mods of the list (the Update topic is keyed by the same values) */
    mods: ModOption[];
    /** Update states of the mods: a prop for testing, the Update topic without */
    update?: UpdateTopicLike;
    /** Click of the update button: replaces the update_apply call when given */
    onUpdate?: () => void;
    /** Click of the cancel button: replaces the cancel_apply call when given */
    onCancel?: () => void;
    class?: string;
  };
  let { mods, update, onUpdate, onCancel, class: className }: $props = $props();

  const updateTopic = useTopic<UpdateTopicLike>("Update");
  const updateApplyRpc = updateTopic.rpc();
  const cancelApplyRpc = updateTopic.rpc();

  const updateData = $derived(update ?? updateTopic.data);

  // states of the update flow that may still be cancelled (the check of a mod,
  // the stop of the workers, the download): 'updating' is past the point of no
  // return, the flow restarts the backend by itself when it finishes
  const UPDATE_CANCELLABLE = ["checking", "queued", "stopping", "downloading"];

  function updateInfo(mod: string) {
    return updateData?.[mod];
  }

  /** The mods with an available update (the scope of the single update entry). */
  const availableMods = $derived(mods.filter((mod) => updateInfo(mod.value)?.state === "available"));
  /** Whether the mod updates are off for this backend (every managed mod is 'disabled'). */
  const updatesDisabled = $derived(mods.some((mod) => updateInfo(mod.value)?.state === "disabled"));
  /** Whether the backend runs without a live supervisor (no mod can be updated). */
  const noSupervisor = $derived(mods.some((mod) => updateInfo(mod.value)?.state === "no-supervisor"));
  /** Whether the update flow of the backend may still be cancelled. */
  const updateCancellable = $derived(
    mods.some((mod) => {
      const state = updateInfo(mod.value)?.state;
      return state !== undefined && UPDATE_CANCELLABLE.includes(state);
    }),
  );

  function handleUpdate() {
    if (onUpdate) {
      onUpdate();
      return;
    }
    updateApplyRpc.call("update_apply", {});
  }

  function handleCancel() {
    if (onCancel) {
      onCancel();
      return;
    }
    cancelApplyRpc.call("cancel_apply", {});
  }
</script>

<div class={cn("flex items-center gap-2", className)}>
  <span class="flex shrink-0 gap-2">
    <Button size="sm" disabled={availableMods.length === 0} onclick={handleUpdate}>
      {t.Update.ButtonUpdate()}
    </Button>
    {#if updateCancellable}
      <Button size="sm" variant="outline" onclick={handleCancel}>
        {t.Update.ButtonCancel()}
      </Button>
    {/if}
  </span>
  <span class="text-muted-foreground truncate text-sm">
    {#if availableMods.length}
      {t.Update.AvailableCount({ count: String(availableMods.length) })}
    {:else if noSupervisor}
      {t.Update.UpdatesNoSupervisor()}
    {:else if updatesDisabled}
      {t.Update.UpdatesDisabled()}
    {:else}
      {t.Update.NoAvailable()}
    {/if}
  </span>
</div>

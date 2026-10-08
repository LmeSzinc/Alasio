<script lang="ts">
  import { goto } from "$app/navigation";
  import type { UpdateTopicLike } from "$lib/components/aside/types";
  import * as AlertDialog from "$lib/components/ui/alert-dialog";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";

  /**
   * "Update available" popup of the update flow (renders only the dialog).
   *
   * Mounted once by the (private) layout: the Update topic subscription must
   * be alive on every page, because a check completes in the background while
   * the user is anywhere. The dialog opens when a mod enters 'available' with
   * a (mod, latest) pair not notified in this session; closing it is not a
   * cancel -- the update stays available (the badge stays on the mod manager
   * page, and the pair is not notified again).
   */

  // (mod, latest) pairs notified in this session: a state push must not
  // reopen the dialog for a pair the user already saw
  const notified = new Set<string>();

  let current = $state<{ mod: string; latest: string } | null>(null);
  let open = $state(false);

  const updateClient = useTopic<UpdateTopicLike>("Update");

  $effect(() => {
    // Read the data unconditionally: the effect must also re-run when the
    // topic data appears or is replaced after a reconnect
    const data = updateClient.data;
    if (!data) return;
    for (const [mod, info] of Object.entries(data)) {
      if (info.state !== "available" || !info.latest_version) continue;
      const key = `${mod}@${info.latest_version}`;
      if (notified.has(key)) continue;
      notified.add(key);
      current = { mod, latest: info.latest_version };
      open = true;
      return;
    }
  });

  function handleView() {
    open = false;
    void goto("/dev/mod");
  }
</script>

<AlertDialog.Root bind:open>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>{t.Update.DialogTitle()}</AlertDialog.Title>
      <AlertDialog.Description>
        {t.Update.DialogDescription({ mod: current?.mod ?? "", version: current?.latest ?? "" })}
      </AlertDialog.Description>
    </AlertDialog.Header>
    <AlertDialog.Footer>
      <AlertDialog.Cancel>{t.Update.DialogLater()}</AlertDialog.Cancel>
      <Button onclick={handleView}>{t.Update.DialogView()}</Button>
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>

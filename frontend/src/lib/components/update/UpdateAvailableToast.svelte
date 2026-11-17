<script lang="ts">
  import { untrack } from "svelte";
  import type { UpdateTopicLike } from "$lib/components/aside/types";
  import { useTopic } from "$lib/ws";
  import { type UpdateToastData, showUpdateToast } from "./updateToast";

  /**
   * Watcher of the Update topic: shows the persistent "update available" toast
   * when a (mod, latest) pair this session has not announced yet appears. It
   * renders nothing.
   *
   * Mounted once by the (private) layout: the subscription has to be alive on
   * every page, because a check completes in the background while the user is
   * anywhere.
   *
   * The toast carries every update that is available at that moment, not only
   * the pair that triggered it: a second available mod extends the rows of the
   * toast instead of replacing the first. showUpdateToast dismisses the toast
   * on screen, so the exit animation of the old one plays under the entrance
   * of the new one.
   *
   * The announcement set is per mount, i.e. per session in production: a mount
   * of the same component with the same data does not announce a pair twice.
   */
  type $props = {
    /** The data of the Update topic: a prop for the preview page, the topic without it */
    update?: UpdateTopicLike;
  };
  let { update }: $props = $props();

  // (mod, latest) pairs announced in this session: a state push must not show
  // the toast again for a pair the user has already seen. Closing the toast is
  // not a cancel -- the update stays available on the updater page.
  const announced = new Set<string>();

  const updateTopic = useTopic<UpdateTopicLike>("Update");
  // The prop replaces the topic data (the preview page fakes the topic), so a
  // mount of the page never announces what the backend reported to the layout
  const updateData = $derived(update ?? updateTopic.data);

  $effect(() => {
    // Read the data unconditionally: the effect must also re-run when the
    // topic data appears (undefined -> available) or is replaced after a
    // reconnect
    const data = updateData;
    if (!data) return;

    // Collect the whole set: the toast announces every available mod, while
    // only a pair not announced yet pops it
    const available: UpdateToastData[] = [];
    let hasNew = false;
    for (const [mod, info] of Object.entries(data)) {
      if (info.state !== "available" || !info.latest_version) continue;
      const key = `${mod}@${info.latest_version}`;
      available.push({ mod, latest: info.latest_version });
      if (!announced.has(key)) hasNew = true;
    }
    if (!hasNew) return;
    // Announce at once: every pair of the set is on screen from here on
    for (const update of available) {
      announced.add(`${update.mod}@${update.latest}`);
    }
    // The sonner state read while creating the toast is not an input of this
    // effect: untrack keeps the toast list from re-running the scan
    untrack(() => showUpdateToast(available));
  });
</script>

<script lang="ts">
  import X from "@lucide/svelte/icons/x";
  import { goto } from "$app/navigation";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import ModVersion from "./ModVersion.svelte";
  import type { UpdateToastData } from "./updateToast";

  /**
   * Content of the "update available" toast: a svelte-sonner custom toast,
   * created by `updateToast.ts` with every update known at the moment.
   *
   * The layout is always the same: the title row, then one row per mod with
   * the name on the left and the ModVersion pill on the right (each side at
   * most half of the row, both truncating). One update reads like five, so
   * the toast never re-flows into another shape as mods join.
   *
   * The toast is rendered by the global Toaster, which injects `closeToast`
   * (sonner renders a custom toast as
   * `<Component {...componentProps} closeToast={deleteToast} />`): calling it
   * plays the exit animation and removes the toast. The close button below is
   * required because sonner renders its own close button only for its
   * built-in toasts, never for a custom one.
   */
  let {
    updates,
    closeToast,
  }: {
    /** The available updates the toast announces, at least one */
    updates: UpdateToastData[];
    /** Injected by sonner: removes the toast with its exit animation */
    closeToast?: () => void;
  } = $props();

  function handleView() {
    // Leaving the toast behind would cover the page the user just asked for
    closeToast?.();
    void goto("/dev/updater");
  }
</script>

<div
  class="bg-popover text-popover-foreground border-border w-[356px] max-w-[calc(100vw-2rem)] rounded-lg border p-4 shadow-lg"
>
  <div class="flex items-center justify-between gap-2">
    <div class="text-sm font-medium">{t.Update.ToastTitle()}</div>
    <Button
      variant="ghost"
      size="icon-sm"
      class="text-muted-foreground -m-1"
      aria-label={t.Update.ToastClose()}
      onclick={() => closeToast?.()}
    >
      <X />
    </Button>
  </div>
  <ul class="mt-2 flex flex-col gap-1 text-sm">
    {#each updates as update (update.mod)}
      <li class="flex items-center justify-between gap-3">
        <span class="max-w-[50%] min-w-0 truncate" title={update.mod}>{update.mod}</span>
        <ModVersion version={update.latest} class="max-w-[50%] shrink" />
      </li>
    {/each}
  </ul>
  <div class="mt-3 flex justify-end">
    <Button size="sm" onclick={handleView}>{t.Update.ToastView()}</Button>
  </div>
</div>

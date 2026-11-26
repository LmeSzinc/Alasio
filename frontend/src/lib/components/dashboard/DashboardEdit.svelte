<script lang="ts">
  import Pencil from "@lucide/svelte/icons/pencil";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { type ArgData, type InputProps, i18nTextToOneLine } from "../arg/utils.svelte";
  import DashboardEditDialog from "./DashboardEditDialog.svelte";
  import { type DashboardChange, type DashboardEditEvent, dashboardEditEvents, dashboardFields } from "./dashboardEdit";

  /**
   * The edit action of a dashboard group: the button right of its value plus the
   * dialog that edits every editable arg of the group (see dashboardFields).
   *
   * A group without an editable arg (a dashboard that only shows computed
   * numbers) renders no button, so every other group of every nav gets the same
   * action without a per-type decision here.
   */
  type Props = {
    /** Args of one dashboard group */
    data: Record<string, ArgData>;
    /** Single-arg edit, only used when the caller has no batch handler */
    handleEdit?: InputProps["handleEdit"];
    /** Batch edit of the changed fields, preferred over handleEdit */
    handleMultiEdit?: (events: DashboardEditEvent[]) => void;
  };
  let { data, handleEdit, handleMultiEdit }: Props = $props();

  const fields = $derived(dashboardFields(data));
  // A dashboard group carries its translated name in `_info`; a group without
  // one (an untranslated example-mod group) is titled by its group name, not by
  // the "_info" arg name
  const name = $derived(i18nTextToOneLine(data._info?.name) || data._info?.group || "");

  // The button and the dialog state live here, so several dashboard groups of
  // one page stay independent of each other
  let editing = $state(false);

  function handleSave(changes: DashboardChange[]) {
    if (handleMultiEdit) {
      handleMultiEdit(dashboardEditEvents(changes));
      return;
    }
    // A caller without a batch handler (the dev debug pages) gets one edit per
    // changed arg, with the value the dialog validated
    for (const change of changes) {
      handleEdit?.({ ...change.data, value: change.value });
    }
  }
</script>

{#if fields.length > 0}
  <Button
    variant="ghost"
    size="icon-xs"
    class="shrink-0 text-muted-foreground"
    aria-label={t.Input.DashboardEdit()}
    title={t.Input.DashboardEdit()}
    onclick={() => (editing = true)}
  >
    <Pencil />
  </Button>
  <DashboardEditDialog bind:open={editing} {name} {fields} onSave={handleSave} />
{/if}

<script lang="ts">
  import { untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { t } from "$lib/i18n";
  import Arg from "../arg/Arg.svelte";
  import type { ArgData } from "../arg/utils.svelte";
  import { type DashboardChange, parseDashboardField, sameField } from "./dashboardEdit";

  /**
   * The editor of one dashboard group: every editable arg of the group, in the
   * attr rows of the config pages.
   *
   * The rows are rendered by Arg: the datatype picks the input and the layout
   * (a number over dt=input-int / input-float, the time over dt=datetime with
   * its local time display and timezone offset, a select over its options), so
   * the dialog edits a field exactly the way a page does.
   *
   * Every edit only touches a draft copy of the args. Any edit of a field moves
   * the record time to now (the value the backend would otherwise recover from
   * the stored time would not be the one the user typed), except an edit of the
   * time itself; a draft that came back to the values it opened with puts the
   * recorded time back. The reset button of a row restores that row to the value
   * the dialog opened with — nothing is committed yet, so there is no default to
   * fall back to. Save commits the changed fields as one batch; cancel only says
   * that edits were discarded when the draft really differs, the same behaviour
   * as the filter-order dialog.
   */
  type Props = {
    open?: boolean;
    /** Name of the dashboard group, a text of multiple lines is flattened by the caller */
    name: string;
    /** Editable args of the group, see dashboardFields() */
    fields: ArgData[];
    /** Commits the changed fields, the caller sends them as one batch */
    onSave: (changes: DashboardChange[]) => void;
  };
  let { open = $bindable(false), name, fields, onSave }: Props = $props();

  // The fields as the dialog opened, and the draft being edited
  let original = $state<ArgData[]>([]);
  let drafts = $state<ArgData[]>([]);
  let wasOpen = $state(false);

  // The field values the draft last reconciled the time with (bookkeeping of
  // the effect below, not state of the dialog)
  let lastSignature = untrack(() => signatureOf(drafts));

  /** Bumped on every edit of a row, so the validity is read back after it */
  let editTick = $state(0);

  /**
   * The args whose row refuses the text it shows, reported by the inputs of the
   * rows. A rejected text never reaches the draft (the input keeps it to
   * itself and only commits a valid value), so this is how the footer knows a
   * field is not ready to be saved.
   */
  let invalidArgs = $state<string[]>([]);

  /**
   * Remember the validity a row reports. Idempotent: the report of a row that
   * keeps its state must not touch the draft state, or the parent would render
   * again and the row would report again (an endless round trip).
   *
   * Args:
   *   arg (str): Name of the arg the row edits
   *   invalid (bool): True while the row refuses its text
   */
  function trackValidity(arg: string, invalid: boolean) {
    const listed = invalidArgs.includes(arg);
    if (invalid === listed) return;
    invalidArgs = invalid ? [...invalidArgs, arg] : invalidArgs.filter((name) => name !== arg);
  }

  /**
   * The values of a field list, but the timestamp, as one comparable string.
   *
   * Any change of it is an edit of the record, whatever arg it belongs to; the
   * timestamp is left out because editing it is not an edit of the record.
   *
   * @param fields Fields of a draft
   * @returns Signature of their values
   */
  function signatureOf(fields: ArgData[]): string {
    return fields
      .filter((field) => field.arg !== "Time")
      .map((field) => `${field.arg}=${field.value}`)
      .join("|");
  }

  $effect.pre(() => {
    // Initialize the draft when the dialog opens; an external change of the
    // args while it is open must not overwrite the pending edits
    if (open && !wasOpen) {
      original = fields.map((field) => ({ ...field }));
      drafts = fields.map((field) => ({ ...field }));
      lastSignature = signatureOf(drafts);
      invalidArgs = [];
    }
    wasOpen = open;
  });

  /** The parsed value of every field, in the order of the draft */
  const results = $derived(drafts.map((draft) => parseDashboardField(draft.value, draft)));
  const blocked = $derived(invalidArgs.length > 0 || results.some((result) => result.error !== null));
  const dirty = $derived(
    drafts.some((draft, index) => {
      const before = original[index];
      return before === undefined || !sameField(draft, before);
    }),
  );

  /**
   * Every edit of a row, or a row losing the focus, invalidates the validity read
   * back below.
   */
  function onRowEdit() {
    editTick += 1;
  }

  /**
   * The reset button of a row: back to the value the dialog opened with.
   * Nothing of the draft was sent yet, so the recorded value of the page (not
   * the backend default) is the one to restore.
   *
   * @param index Position of the field in the draft
   */
  function resetField(index: number) {
    const before = original[index];
    if (before === undefined) return;
    drafts[index].value = before.value;
    // The row clears its own error with the value, so the footer has to read
    // the rows again before it decides whether the form can be saved
    editTick += 1;
  }

  // Every edit of a field is an edit of the record, so it moves the time to now:
  // the value the backend would otherwise recover from the stored time is not
  // the one the user typed. Editing the time itself is the one case that does
  // not touch it — that is what the row is for. A draft that came back to the
  // values the dialog opened with (a reset, or the old text typed again) is not
  // an edit anymore, so the time goes back to the recorded one with it.
  $effect(() => {
    const next = signatureOf(drafts);
    if (next === lastSignature) return;
    lastSignature = next;
    const timeIndex = drafts.findIndex((draft) => draft.arg === "Time");
    if (timeIndex < 0) return;
    const unchanged = drafts.every((draft, index) => {
      const before = original[index];
      return draft.arg === "Time" || before === undefined || sameField(draft, before);
    });
    drafts[timeIndex].value = unchanged ? original[timeIndex]?.value : new Date().toISOString();
  });

  function save() {
    // A row that still refuses the text it shows keeps the dialog open: the
    // error is displayed under the field, and the invalid text may not have
    // reached the draft yet
    if (blocked) return;
    const changes: DashboardChange[] = [];
    for (const [index, draft] of drafts.entries()) {
      const before = original[index];
      if (before !== undefined && sameField(draft, before)) continue;
      changes.push({ data: draft, value: results[index].value });
    }
    if (changes.length > 0) onSave(changes);
    open = false;
  }

  function cancel() {
    // A draft equal to the record has nothing to discard, so it stays silent
    if (dirty) {
      toast.info(t.Input.DashboardEditsDiscarded(), { duration: 2000 });
    }
    open = false;
  }

  // Every close request that comes from the dialog itself (escape, overlay
  // click, close button) is a cancel; saving closes the dialog programmatically,
  // which does not pass through here
  function onOpenChange(next: boolean) {
    if (!next) cancel();
  }
</script>

<Dialog bind:open {onOpenChange}>
  <!--
    The arg rows inside the dialog are the rows of the page, so their input
    column must keep the width it has there: the layout gives it `w-9/20`
    capped at `max-w-50` (see LayoutHorizontal), and 45% of a narrow dialog
    stays below that cap, which would shrink every field. 520px leaves ~472px
    of inner width (p-6), so 45% of a row passes the 12.5rem cap and both fields
    read exactly as wide as the ones on the page.
  -->
  <DialogContent class="flex max-h-[85vh] flex-col sm:max-w-[520px]">
    <DialogHeader>
      <DialogTitle>{name}</DialogTitle>
    </DialogHeader>

    <!-- The rows of the arg pages: Arg picks the input and the layout of the
         datatype, so the draft is edited exactly like the arg itself. The reset
         button of a row restores that row to the value it opened with. `input`
         and `focusout` bubble here from the rows (blur does not), which is how
         the footer learns that a field turned invalid.

         The scroll port pads its rows: the focus ring of an input is drawn 7px
         outside its box (ring-2 at ring-offset-5), and a scroll port clips what
         its children paint at its own edges, so an unpadded one would cut the
         ring of the first and last row and of the right column. 12px clears
         the ring, and keeps the input column at the width it has on a page
         (45% of 448px is still above the 12.5rem cap). -->
    <div class="flex min-h-0 flex-1 flex-col gap-y-2 overflow-y-auto p-3" oninput={onRowEdit} onfocusout={onRowEdit}>
      {#each drafts as draft, index (draft.arg)}
        <!-- A focused row lifts above its neighbours, so its focus ring is never
             painted under the row below it -->
        <div data-slot="dashboard-edit-row" data-arg={draft.arg} class="relative focus-within:z-10">
          <Arg
            bind:data={drafts[index]}
            handleReset={() => resetField(index)}
            onInvalidChange={(invalid) => trackValidity(draft.arg, invalid)}
          />
        </div>
      {/each}
    </div>

    <DialogFooter>
      <Button variant="outline" onclick={cancel}>{t.Input.Cancel()}</Button>
      <Button onclick={save} disabled={blocked}>{t.Input.Save()}</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>

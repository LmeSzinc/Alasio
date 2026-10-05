<script lang="ts">
  import { type InputProps, useArgValue } from "$lib/components/arg/utils.svelte";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import FilterOrderDialog from "./FilterOrderDialog.svelte";

  /**
   * The row action of dt="filter-order": the always visible edit button of the
   * title row, plus the editor dialog it opens (see
   * doc/2026-10-03_filter-order.md §4.2). The button and the dialog state live
   * in this component, so several filter-order rows on one page stay
   * independent of each other.
   */
  let { data = $bindable(), handleEdit, handleReset }: InputProps = $props();

  const arg = $derived(useArgValue<string[]>(data));
  let editing = $state(false);

  function handleSave(next: string[]) {
    arg.value = next;
    // submit() does the dirty check, the optimistic update and the rpc call,
    // the same as dt=select
    arg.submit(handleEdit);
  }

  /**
   * The reset of the dialog is the single-arg reset of this row, the same call
   * the reset button of dt=input makes: `arg.reset` keeps the local value in
   * sync, `handleReset` sends the "reset" rpc (ConfigArg) with the
   * (task, group, arg) of this row.
   */
  function handleResetDefault() {
    arg.reset(handleReset);
  }
</script>

<Button variant="ghost" size="sm" class="h-7" onclick={() => (editing = true)}>
  {t.Input.FilterOrderEdit()}
</Button>

<FilterOrderDialog bind:open={editing} {data} value={arg.value} onSave={handleSave} onReset={handleResetDefault} />

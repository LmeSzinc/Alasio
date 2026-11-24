<script lang="ts">
  import Copy from "@lucide/svelte/icons/copy";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { t } from "$lib/i18n";

  /**
   * The marks of a row: a repeated item and an item that is not legal anymore.
   *
   * Both are a small icon with a tooltip, and a row may carry either, both or
   * neither, so the two flags are the whole contract. The icons keep their own
   * colour and label: a repeat is amber (the item is legal, its position is not)
   * and an invalid item is the destructive colour.
   */
  type Props = {
    /** True for an item repeated from an earlier row */
    duplicate?: boolean;
    /** True for an item that is not a legal item anymore */
    invalid?: boolean;
  };
  let { duplicate = false, invalid = false }: Props = $props();
</script>

{#if duplicate}
  <Tooltip.Provider>
    <Tooltip.Root>
      <Tooltip.Trigger>
        <span role="img" class="inline-flex" aria-label={t.Input.FilterOrderDuplicate()}>
          <Copy class="size-3 shrink-0 text-amber-600 dark:text-amber-400" />
        </span>
      </Tooltip.Trigger>
      <Tooltip.Content>
        <p>{t.Input.FilterOrderDuplicate()}</p>
      </Tooltip.Content>
    </Tooltip.Root>
  </Tooltip.Provider>
{/if}
{#if invalid}
  <Tooltip.Provider>
    <Tooltip.Root>
      <Tooltip.Trigger>
        <span role="img" class="inline-flex" aria-label={t.Input.FilterOrderInvalid()}>
          <TriangleAlert class="size-3 shrink-0 text-destructive" />
        </span>
      </Tooltip.Trigger>
      <Tooltip.Content>
        <p>{t.Input.FilterOrderInvalid()}</p>
      </Tooltip.Content>
    </Tooltip.Root>
  </Tooltip.Provider>
{/if}

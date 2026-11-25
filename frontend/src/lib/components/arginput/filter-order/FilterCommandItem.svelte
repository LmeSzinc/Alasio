<script lang="ts">
  import { useDraggable } from "@dnd-kit-svelte/core";
  import Check from "@lucide/svelte/icons/check";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";

  /**
   * One declared command of a filter arg, in the command list below the list
   *
   * The chip is draggable while the order does not hold it yet: dragging it into
   * the list inserts the command where it is dropped, and the plus button
   * appends it at the end. A command is unique, so a command the order already
   * holds shows a check instead of the plus and the chip is dimmed.
   *
   * A used command gets no drag at all: the drop handler of the dialog refuses
   * the duplicate, so a drag that starts on its grip can only draw the drop line
   * of the list and then insert nothing. The grip says so instead of offering a
   * gesture that leads nowhere, the row in the list is the one to move.
   */
  type Props = {
    /** The command, as declared by the arg */
    command: string;
    /** The translated name of the command */
    label: string;
    /** True when the order already holds the command */
    used?: boolean;
    /** Appends the command to the order */
    onAdd?: () => void;
  };
  let { command, label, used = false, onAdd }: Props = $props();

  // The drag carries the command itself, and no index: a drop inserts it into
  // the list at the drop target instead of moving a row
  // svelte-ignore state_referenced_locally
  const { attributes, listeners, isDragging, setNodeRef } = useDraggable({
    id: `command-${command}`,
    data: { type: "item", item: command },
  });

  // A used command binds no listener: the drag would be measured and its drop
  // line drawn, only for the drop to be refused. `listeners` follows the flag,
  // so a command the draft gains or loses takes the grip with it.
  const gripListeners = $derived(used ? {} : listeners.current);
  const canDrag = $derived(!used);
</script>

<div
  use:setNodeRef
  data-dragging={isDragging.current}
  data-used={used}
  data-slot="filter-command"
  class={cn(
    "drag-placeholder relative flex h-6 shrink-0 items-center gap-0.5 rounded-md border bg-card pr-0.5 pl-0.5 text-xs",
    used && "bg-muted text-muted-foreground",
  )}
>
  <!-- Only the grip starts a drag, and only while the command is still unused -->
  <div
    {...attributes.current}
    {...gripListeners}
    role="button"
    tabindex={canDrag ? 0 : -1}
    aria-disabled={!canDrag}
    class={cn(
      "flex h-5 shrink-0 items-center px-0.5 text-muted-foreground",
      canDrag ? "cursor-grab" : "cursor-not-allowed opacity-60",
    )}
    aria-label={`${t.Input.FilterOrderDrag()} ${label}`}
  >
    <GripVertical class="size-3.5" />
  </div>
  <span class="max-w-40 min-w-0 truncate px-0.5" data-slot="filter-command-label">{label}</span>
  <Button
    variant="ghost"
    size="icon-xs"
    class="size-5 shrink-0"
    disabled={used}
    aria-label={used ? `${t.Input.FilterOrderAdded()} ${label}` : `${t.Input.FilterOrderAdd()} ${label}`}
    onclick={onAdd}
  >
    {#if used}
      <Check />
    {:else}
      <Plus />
    {/if}
  </Button>
</div>

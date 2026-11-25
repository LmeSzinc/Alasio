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
   * The chip is always draggable: dragging it into the list inserts the command
   * where it is dropped, and the plus button appends it at the end. A command is
   * unique, so a command the order already holds shows a check instead of the
   * plus and the chip is dimmed; dragging it in is a no-op (the drop handler of
   * the dialog refuses a duplicate), the row in the list is the one to move.
   *
   * The grip stays active for a used command on purpose: a disabled draggable
   * would bind no listeners at all, and a grip that silently does nothing is
   * worse than a drop that is ignored.
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
  <!-- Only the grip starts a drag -->
  <div
    {...listeners.current}
    {...attributes.current}
    class={cn("flex h-5 shrink-0 cursor-grab items-center px-0.5 text-muted-foreground", used && "opacity-60")}
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

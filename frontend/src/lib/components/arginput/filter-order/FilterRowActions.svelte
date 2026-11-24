<script lang="ts">
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";

  /**
   * The row buttons of a single column list: the two halves of one diamond that
   * move the row up / down, and the remove button.
   *
   * The component owns the buttons only, so its contract is the buttons: the
   * label they speak, whether a move is possible, and what to call. Where the row
   * sits in its list is the caller's business.
   */
  type Props = {
    /** Text of the row in the accessible labels, e.g. "Move up {label}" */
    label: string;
    /** True when the row has a neighbour above it in its list */
    canMoveUp?: boolean;
    /** True when the row has a neighbour below it in its list */
    canMoveDown?: boolean;
    onMoveUp?: () => void;
    onMoveDown?: () => void;
    onRemove?: () => void;
  };
  let { label, canMoveUp = false, canMoveDown = false, onMoveUp, onMoveDown, onRemove }: Props = $props();
</script>

<!-- No gap between the row buttons, the row stays compact -->
<div class="flex items-center">
  <!-- Up / down are the two halves of one diamond: two triangles that share
       their waist, so the control reads as a single rotated square while each
       half stays its own button (each one highlights and disables on its own).
       The shape is the SVG fill, the icon keeps the size and the gap of the
       outlined variant -->
  <div class="flex shrink-0 flex-col gap-px">
    <button
      type="button"
      class="flex h-2 w-4 items-center justify-center text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground disabled:pointer-events-none disabled:text-muted-foreground/25"
      disabled={!canMoveUp}
      aria-label={`${t.Input.FilterOrderMoveUp()} ${label}`}
      onclick={onMoveUp}
    >
      <!-- Filled triangle: the path is closed and painted with the current text
           color, so hover / disabled follow the row -->
      <svg viewBox="0 0 12 6" class="h-1.5 w-3" aria-hidden="true">
        <path d="M6 1 11 5H1z" fill="currentColor" stroke="none" />
      </svg>
    </button>
    <button
      type="button"
      class="flex h-2 w-4 items-center justify-center text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground disabled:pointer-events-none disabled:text-muted-foreground/25"
      disabled={!canMoveDown}
      aria-label={`${t.Input.FilterOrderMoveDown()} ${label}`}
      onclick={onMoveDown}
    >
      <svg viewBox="0 0 12 6" class="h-1.5 w-3" aria-hidden="true">
        <path d="M1 1h10L6 5z" fill="currentColor" stroke="none" />
      </svg>
    </button>
  </div>
  <Button variant="ghost" size="icon-xs" aria-label={`${t.Input.FilterOrderRemove()} ${label}`} onclick={onRemove}>
    <Trash2 />
  </Button>
</div>

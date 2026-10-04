/**
 * Pure helpers of the dt="filter-order" input.
 *
 * dt="filter-order" is an ordered subset of "option" (e.g. the sortie order
 * `Fleet-1 > Fleet-2 > Submarine`). The transfer-like dialog keeps every list
 * operation in these functions, so the drop math (cross-column moves, same-list
 * reordering, dropping on a column) is unit-testable without a DOM. The cut of
 * the pill lines on the settings page (`cutPills`) is math on a measurement for
 * the same reason.
 */

/** An item of a filter-order value, a python literal item of "option". */
export type FilterOrderItem = string;

/**
 * Compare two item lists, the order is part of the value.
 *
 * @param a The first list
 * @param b The second list
 * @returns True when both lists carry the same items in the same order
 */
export function sameList(a: readonly FilterOrderItem[], b: readonly FilterOrderItem[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((item, index) => item === b[index]);
}

/**
 * Move the item at `index` by `delta` positions in a copy of the list.
 * A move that would leave the list is a no-op, so the caller can wire the
 * up / down buttons without checking the ends itself.
 *
 * @param list The list to reorder
 * @param index Index of the item to move
 * @param delta Offset, -1 for up and 1 for down
 * @returns A new list with the item moved
 */
export function moveItem(list: readonly FilterOrderItem[], index: number, delta: number): FilterOrderItem[] {
  const next = [...list];
  const to = index + delta;
  if (index < 0 || index >= next.length) return next;
  if (to < 0 || to >= next.length) return next;
  const [item] = next.splice(index, 1);
  next.splice(to, 0, item);
  return next;
}

/**
 * Append an item to the end of the list, an item already in the list is not
 * added twice.
 *
 * @param list The list to append to
 * @param item The item to append
 * @returns A new list with the item appended
 */
export function appendItem(list: readonly FilterOrderItem[], item: FilterOrderItem): FilterOrderItem[] {
  if (list.includes(item)) return [...list];
  return [...list, item];
}

/**
 * Remove every occurrence of an item from the list.
 *
 * @param list The list to remove from
 * @param item The item to remove
 * @returns A new list without the item
 */
export function removeItem(list: readonly FilterOrderItem[], item: FilterOrderItem): FilterOrderItem[] {
  return list.filter((value) => value !== item);
}

/**
 * The normalized drop target of a drag, built from the dnd callback:
 * - a row of the selected column: `itemId` is the row, insert before / after it
 * - the selected column container: no `itemId`, the top area inserts first,
 *   the rest appends last
 * - the unused column: the item leaves the order, the position is meaningless
 */
export type DropTarget =
  { kind: "selected"; itemId?: FilterOrderItem; position: "top" | "bottom" } | { kind: "unused" };

/**
 * Apply a drop: move `item` to `target` in a copy of the draft.
 *
 * @param draft The current selected order
 * @param item The item being dragged, it may come from either column
 * @param target The normalized drop target
 * @returns The new selected order
 */
export function applyDrop(
  draft: readonly FilterOrderItem[],
  item: FilterOrderItem,
  target: DropTarget,
): FilterOrderItem[] {
  // A move is always a removal first: the item may come from either column
  const from = draft.indexOf(item);
  const next = draft.filter((value) => value !== item);

  if (target.kind === "unused") return next;

  // Dropping the row onto itself must not move it
  if (target.itemId === item) return [...draft];

  let to: number;
  if (target.itemId === undefined) {
    // The whole column is the target: the top area inserts first, the rest appends
    to = target.position === "top" ? 0 : next.length;
  } else {
    // A row is the target: insert before / after it
    to = draft.indexOf(target.itemId);
    if (to === -1) {
      // The row vanished (should not happen while dragging): append, never drop the item
      to = next.length;
    } else {
      if (from !== -1 && from < to) to -= 1;
      if (target.position === "bottom") to += 1;
    }
  }
  next.splice(Math.min(Math.max(to, 0), next.length), 0, item);
  return next;
}

/**
 * The geometry of one pill of the measured line: the top of the line the pill
 * wrapped into, and the right edge of the pill.
 */
export type PillBox = { top: number; right: number };

/**
 * How many pills fit into the first "maxLines" lines when the rest is replaced
 * by the "> ..." marker. The marker has to end the line of the last visible
 * pill, so pills are dropped from that line (and from the one before it, if the
 * line is too narrow) until the marker fits next to the pill kept. At least one
 * pill is always shown, in a very narrow line the marker wraps below it.
 *
 * Math on the geometry of every pill, measured once on a full render: the cut
 * needs no render of its own, the caller never shows an intermediate state.
 *
 * @param boxes The box of every pill, in order
 * @param markerWidth Width of the marker
 * @param gap The column gap between two pills of a line
 * @param edge The right edge the pills wrap at, in the coordinates of the boxes
 * @param maxLines Max number of lines to fill
 * @returns The number of pills to show
 */
export function cutPills(
  boxes: readonly PillBox[],
  markerWidth: number,
  gap: number,
  edge: number,
  maxLines: number,
): number {
  // How many pills fit into the first maxLines lines, grouped by the top of the
  // line they wrapped into
  const lines = new Set<number>();
  let count = 0;
  for (const box of boxes) {
    if (!lines.has(box.top)) {
      if (lines.size >= maxLines) break;
      lines.add(box.top);
    }
    count++;
  }
  // Everything fits, there is no marker to place
  if (count === boxes.length) return count;
  // The marker takes the place of the pills behind the cut, it has to stay next
  // to the last visible pill (dropping one can walk back into the line before)
  while (count > 1 && boxes[count - 1].right + gap + markerWidth > edge) count--;
  return count;
}

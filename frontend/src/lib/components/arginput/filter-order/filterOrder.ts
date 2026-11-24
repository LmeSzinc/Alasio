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
 * The datatypes that share the ordered transfer editor, they differ in what an
 * item is:
 *
 * - filter-order:      one level, an item is bought once
 * - filter-order-item: two levels, the primary item is the identity
 * - filter-order-pair: two levels, the whole "item-option" is the identity
 *
 * dt="filter-regex" uses the single column editor of its own (see
 * FilterRegexEditor.svelte), it is not part of this union.
 */
export type FilterOrderDt = "filter-order" | "filter-order-item" | "filter-order-pair";

/** True when the value is written as two levels, "item-option" */
export function isTwoLevel(dt: string | undefined): boolean {
  return dt === "filter-order-item" || dt === "filter-order-pair";
}

/** True when a row carries an editable secondary option, so the identity is its primary item */
export function hasSecondary(dt: string | undefined): boolean {
  return dt === "filter-order-item";
}

/** True when the unused column is grouped by the primary item, the identity is the whole pair */
export function isGrouped(dt: string | undefined): boolean {
  return dt === "filter-order-pair";
}

/** True when the value may hold optionCommand, entries that carry no item at all */
export function hasTokens(dt: string | undefined): boolean {
  return dt === "filter-order-pair";
}

/** True when an item is decoded by "pattern" into one value per level */
export function isRegex(dt: string | undefined): boolean {
  return dt === "filter-regex";
}

/**
 * Separator between the levels of an item, e.g. "PlateT4-gun".
 *
 * Fixed for every arg, mirroring FILTER_ORDER_SEPARATOR of the backend: the
 * value is parsed by the regex of the mod, one capture group per level, so the
 * separator cannot be chosen by the arg. An item of an arg without secondary
 * options is never split, so it may contain it.
 */
export const SECONDARY_SEPARATOR = "-";

/**
 * Split a filter-order item into its primary item and its secondary option.
 *
 * A primary item without a secondary option stays itself, e.g. "Chip". The
 * item is split on the first separator, so the secondary option keeps any
 * later one. Mirrors split_option() of the backend.
 *
 * @param item The item of the value
 * @returns The primary item and the secondary option, null when the item has none
 */
export function splitOption(item: string): [string, string | null] {
  const at = item.indexOf(SECONDARY_SEPARATOR);
  if (at === -1) return [item, null];
  return [item.slice(0, at), item.slice(at + 1)];
}

/**
 * The primary item of an item, the part before the secondary separator.
 *
 * @param item The item of the value
 * @returns The primary item
 */
export function primaryOf(item: string): string {
  return splitOption(item)[0];
}

/**
 * Build an item from a primary item and its secondary option.
 *
 * @param primary The primary item
 * @param secondary The secondary option, null or empty for a primary without one
 * @returns The item of the value
 */
export function composeOption(primary: string, secondary: string | null | undefined): string {
  if (secondary === null || secondary === undefined || secondary === "") return primary;
  return `${primary}${SECONDARY_SEPARATOR}${secondary}`;
}

/**
 * The legal items of a filter-order value: every primary item of "option",
 * expanded with its own secondary options when the datatype is two level.
 * Mirrors expand_filter_order_items() of the backend, so a row the dialog
 * creates is exactly what the generated literal accepts. The optionCommand of a
 * filter-order-pair are legal items too: they are stored as themselves, so a
 * value holding "shortest" is not an invalid item.
 *
 * @param option Primary items of the arg
 * @param optionDict Secondary options of each primary item
 * @param dt Datatype of the arg
 * @param optionCommand Tokens of the arg, entries that carry no item at all
 * @returns The legal items
 */
export function validItems(
  option: readonly any[] | null | undefined,
  optionDict: Record<string, any[]> | null | undefined,
  dt: string | undefined,
  optionCommand: readonly any[] | null | undefined,
): Set<string> {
  const twoLevel = isTwoLevel(dt);
  const out = new Set<string>();
  for (const item of option ?? []) {
    const primary = String(item);
    const secondaries = twoLevel ? optionDict?.[primary] : null;
    if (secondaries && secondaries.length > 0) {
      for (const secondary of secondaries) out.add(composeOption(primary, String(secondary)));
    } else {
      out.add(primary);
    }
  }
  if (hasTokens(dt)) {
    for (const command of optionCommand ?? []) out.add(String(command));
  }
  return out;
}

/**
 * The item added to the order when a primary item is picked from the unused
 * column: its first secondary option, or the bare item when it has none.
 *
 * @param primary The primary item
 * @param optionDict Secondary options of each primary item
 * @returns The item to add
 */
export function defaultItem(primary: string, optionDict?: Record<string, any[]> | null): string {
  const secondaries = optionDict?.[primary];
  if (secondaries && secondaries.length > 0) return composeOption(primary, String(secondaries[0]));
  return primary;
}

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
 * The positions of the repeated occurrences in the list.
 *
 * A stored value may repeat an item when it was written before the datatype was
 * enforced, or by hand. The backend rejects such a value, so the editor marks
 * the rows that claim a position already taken: the first occurrence is where
 * the item belongs, every later one is the leftover. Marking all of them would
 * blame the row the user most likely wants to keep.
 *
 * @param list The list to inspect
 * @returns The indexes that are their item's second (or later) occurrence
 */
export function duplicateIndexes(list: readonly FilterOrderItem[]): Set<number> {
  const seen = new Set<string>();
  const out = new Set<number>();
  list.forEach((item, index) => {
    if (seen.has(item)) out.add(index);
    else seen.add(item);
  });
  return out;
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
 * Remove the item at `index` from the list.
 *
 * Index based, not value based: a stored value may repeat an item, and every
 * row operation has to touch the row the user picked, not its first occurrence.
 *
 * @param list The list to remove from
 * @param index Index of the item to remove
 * @returns A new list without that one item
 */
export function removeAt(list: readonly FilterOrderItem[], index: number): FilterOrderItem[] {
  const next = [...list];
  if (index < 0 || index >= next.length) return next;
  next.splice(index, 1);
  return next;
}

/**
 * Remove every occurrence of an item from the list.
 *
 * Only for a caller that really means "this value may not appear at all"; a
 * row operation must use removeAt(), otherwise a repeated item loses the valid
 * occurrence together with the one being deleted.
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
 * - a row of the selected column: `index` is the row, insert before / after it
 * - the selected column container: no `index`, the top area inserts first, the
 *   rest appends last
 * - the unused column: the row leaves the order, the position is meaningless
 *
 * Rows are addressed by index, never by value: a stored value may repeat an
 * item, and two rows with the same value would otherwise move together.
 */
export type DropTarget = { kind: "selected"; index?: number; position: "top" | "bottom" } | { kind: "unused" };

/**
 * Where a drop inserts, as an index of the list after the dragged row is out.
 *
 * @param count Length of the list without the dragged row
 * @param target The normalized drop target
 * @param from Index the dragged row had, -1 when it comes from the unused column
 * @returns The index to insert at
 */
function insertIndex(count: number, target: { index?: number; position: "top" | "bottom" }, from: number): number {
  if (target.index === undefined) return target.position === "top" ? 0 : count;
  let to = target.index;
  if (from !== -1 && from < to) to -= 1;
  if (target.position === "bottom") to += 1;
  return Math.min(Math.max(to, 0), count);
}

/**
 * Move the row at `from` to `target` in a copy of the draft.
 *
 * @param draft The current selected order
 * @param from Index of the row being dragged
 * @param target The normalized drop target
 * @returns The new selected order
 */
export function moveRow(draft: readonly FilterOrderItem[], from: number, target: DropTarget): FilterOrderItem[] {
  if (from < 0 || from >= draft.length) return [...draft];
  if (target.kind === "unused") return removeAt(draft, from);
  // Dropping the row onto itself must not move it
  if (target.index === from) return [...draft];
  const next = removeAt(draft, from);
  next.splice(insertIndex(next.length, target, from), 0, draft[from]);
  return next;
}

/**
 * Insert an item of the unused column into the draft at the drop target.
 *
 * @param draft The current selected order
 * @param item The item being added, already completed by the caller
 * @param target The normalized drop target
 * @returns The new selected order
 */
export function insertRow(
  draft: readonly FilterOrderItem[],
  item: FilterOrderItem,
  target: DropTarget,
): FilterOrderItem[] {
  const next = [...draft];
  if (target.kind === "unused") return next;
  next.splice(insertIndex(next.length, target, -1), 0, item);
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

/**
 * The select item that clears one level of a dt="filter-regex" row.
 *
 * The stored value of "any" is the empty string, which is not a selectable item
 * of the select primitive, so the trigger maps it to this sentinel.
 */
export const ANY_ITEM = "\u0000any";

/** The level values of one item, an unset level is the empty string */
export type RegexValues = Record<string, string>;

/** One candidate table entry of a level: the level values it is keyed by */
export type RegexLevelEntry = { key: RegexValues; option: string[] };

/** One level of a dt="filter-regex", already normalized by the backend */
export type RegexLevel = { name: string; option: RegexLevelEntry[] };

/**
 * Compiled decode patterns, by the pattern string
 *
 * `new RegExp()` compiles its source on every call, and the pattern of a
 * research filter is several hundred characters long: the editor decodes every
 * row on every render, so compiling there would recompile it hundreds of times
 * per keystroke. An app holds a handful of filter args, the map stays tiny.
 */
const COMPILED_PATTERNS = new Map<string, RegExp | null>();

/**
 * The compiled form of a decode pattern, or null when it does not compile
 *
 * @param pattern The decode pattern
 * @returns The regex that reads one item, null when it can not be compiled
 */
function compileRegex(pattern: string): RegExp | null {
  const cached = COMPILED_PATTERNS.get(pattern);
  if (cached !== undefined) return cached;
  let regex: RegExp | null = null;
  try {
    regex = new RegExp(`^(?:${pattern})$`);
  } catch {
    // A pattern the browser can not compile reads nothing, the caller shows
    // the item as undecodable instead of throwing in a render
    regex = null;
  }
  COMPILED_PATTERNS.set(pattern, regex);
  return regex;
}

/**
 * Decode one item of a dt="filter-regex" into its level values.
 *
 * The pattern is the decoder written by the mod author: one named group per
 * level, the whole item matched. An unset group reads as the empty string, the
 * same as the backend's `_decode_filter_regex()`.
 *
 * @param pattern The decode pattern, in javascript named group syntax
 * @param item The item of the value
 * @returns The level values, null when the pattern does not read the item
 */
export function decodeRegex(pattern: string | null | undefined, item: string): RegexValues | null {
  if (!pattern) return null;
  const regex = compileRegex(pattern);
  if (!regex) return null;
  const match = regex.exec(item);
  if (!match) return null;
  const values: RegexValues = {};
  for (const [name, value] of Object.entries(match.groups ?? {})) values[name] = value ?? "";
  return values;
}

/**
 * Encode the level values of one item: the levels in order, an empty level is
 * skipped (it means "any"), so no separator is left around it.
 *
 * @param levels The levels of the arg, in the declared order
 * @param values The value of every level
 * @returns The item of the value
 */
export function encodeRegex(levels: readonly RegexLevel[], values: RegexValues): string {
  return levels
    .map((level) => values[level.name] ?? "")
    .filter((value) => value !== "")
    .join(SECONDARY_SEPARATOR);
}

/**
 * The candidates of one level for the current selection.
 *
 * Every table entry whose key is covered by the selection applies; the one
 * keyed by the most levels wins, and the entry keyed by nothing is the fallback
 * of the level. The value already selected is always offered, so a stored item
 * that is not a candidate anymore stays selectable.
 *
 * @param level The level
 * @param values The value of every level
 * @returns The candidates of this level
 */
export function regexCandidates(level: RegexLevel, values: RegexValues): string[] {
  let best: RegexLevelEntry | null = null;
  let bestSize = -1;
  for (const entry of level.option ?? []) {
    const names = Object.keys(entry.key);
    if (names.some((name) => (values[name] ?? "") !== entry.key[name])) continue;
    if (names.length > bestSize) {
      best = entry;
      bestSize = names.length;
    }
  }
  return best ? [...best.option] : [];
}

/**
 * The item that results from changing one level of an item, the other levels
 * are kept as they are.
 *
 * @param levels The levels of the arg
 * @param pattern The decode pattern
 * @param item The item being edited
 * @param name The level to change
 * @param value The new value of the level, empty for "any"
 * @returns The new item, null when the item can not be decoded
 */
export function setRegexLevel(
  levels: readonly RegexLevel[],
  pattern: string | null | undefined,
  item: string,
  name: string,
  value: string,
): string | null {
  const values = decodeRegex(pattern, item);
  if (!values) return null;
  values[name] = value;
  return encodeRegex(levels, values);
}

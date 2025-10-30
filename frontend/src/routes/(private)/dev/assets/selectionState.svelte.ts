/**
 * A generic, reusable class for managing selection state in a list.
 * It can handle any type of item, as long as a unique key can be extracted from it.
 * @template T The type of the items in the selection.
 */
export class SelectionState<T> {
  selectedItems = $state<T[]>([]);
  lastSelected = $state<T | null>(null);

  private getKey: (item: T) => string | number;

  /**
   * @param getKey A function that returns a unique identifier for an item of type T.
   */
  constructor(getKey: (item: T) => string | number) {
    this.getKey = getKey;
  }

  /**
   * Checks if two items are the same based on their unique key.
   */
  private isSameItem(a: T, b: T): boolean {
    if (!a || !b) return false;
    return this.getKey(a) === this.getKey(b);
  }

  /**
   * Toggles the selection of a single item.
   */
  toggle(item: T): void {
    const index = this.selectedItems.findIndex((selected) => this.isSameItem(selected, item));

    if (index >= 0) {
      this.selectedItems.splice(index, 1);
    } else {
      this.selectedItems.push(item);
    }
    this.lastSelected = item;
  }

  /**
   * Selects a single item, replacing any previous selection.
   */
  select(item: T): void {
    this.selectedItems = [item];
    this.lastSelected = item;
  }

  /**
   * Selects a range of items between the last selected item and the target item (for Shift+Click).
   */
  selectRange(items: T[], targetItem: T): void {
    if (!this.lastSelected) {
      this.select(targetItem);
      return;
    }
    const lastIndex = items.findIndex((item) => this.isSameItem(item, this.lastSelected!));
    const targetIndex = items.findIndex((item) => this.isSameItem(item, targetItem));

    if (lastIndex === -1 || targetIndex === -1) {
      this.select(targetItem);
      return;
    }

    const start = Math.min(lastIndex, targetIndex);
    const end = Math.max(lastIndex, targetIndex);
    const rangeItems = items.slice(start, end + 1);

    // Keep existing selections made with Ctrl/Cmd key, and add the new range.
    const currentSelection = this.selectedItems.filter(
      (sel) => !rangeItems.some((rangeItem) => this.isSameItem(sel, rangeItem)),
    );
    this.selectedItems = [...currentSelection, ...rangeItems];
    this.lastSelected = targetItem;
  }

  /**
   * Checks if a specific item is currently selected.
   */
  isSelected(item: T): boolean {
    return this.selectedItems.some((selected) => this.isSameItem(selected, item));
  }

  /**
   * Clears all selections.
   */
  clear(): void {
    this.selectedItems = [];
    this.lastSelected = null;
  }

  /**
   * Returns the count of selected items.
   */
  get count(): number {
    return this.selectedItems.length;
  }
}

type SelectedItem = { type: "folder"; name: string } | { type: "resource"; name: string };

class SelectionState {
  selectedItems = $state<SelectedItem[]>([]);
  lastSelected = $state<SelectedItem | null>(null);

  /**
   * Toggle selection of an item
   */
  toggle(item: SelectedItem): void {
    const index = this.selectedItems.findIndex((selected) => this.isSameItem(selected, item));

    if (index >= 0) {
      // Item is already selected, remove it
      this.selectedItems.splice(index, 1);
    } else {
      // Item is not selected, add it
      this.selectedItems.push(item);
    }
    this.lastSelected = item;
  }

  /**
   * Select an item (replacing current selection)
   */
  select(item: SelectedItem): void {
    this.selectedItems = [item];
    this.lastSelected = item;
  }

  /**
   * Add an item to selection without replacing
   */
  add(item: SelectedItem): void {
    if (!this.isSelected(item)) {
      this.selectedItems.push(item);
    }
    this.lastSelected = item;
  }

  /**
   * Select a range of items (for Shift+Click)
   */
  selectRange(items: SelectedItem[], targetItem: SelectedItem): void {
    if (!this.lastSelected) {
      // No previous selection, just select the target
      this.select(targetItem);
      return;
    }

    const lastIndex = items.findIndex((item) => this.isSameItem(item, this.lastSelected!));
    const targetIndex = items.findIndex((item) => this.isSameItem(item, targetItem));

    if (lastIndex === -1 || targetIndex === -1) {
      // Can't find indices, just select the target
      this.select(targetItem);
      return;
    }

    // Select all items between lastIndex and targetIndex (inclusive)
    const start = Math.min(lastIndex, targetIndex);
    const end = Math.max(lastIndex, targetIndex);
    const rangeItems = items.slice(start, end + 1);

    // Add all items in range to selection
    this.selectedItems = [...this.selectedItems];
    for (const item of rangeItems) {
      if (!this.isSelected(item)) {
        this.selectedItems.push(item);
      }
    }
    this.lastSelected = targetItem;
  }

  /**
   * Check if an item is selected
   */
  isSelected(item: SelectedItem): boolean {
    return this.selectedItems.some((selected) => this.isSameItem(selected, item));
  }

  /**
   * Check if a folder is selected
   */
  isFolderSelected(folderName: string): boolean {
    return this.isSelected({ type: "folder", name: folderName });
  }

  /**
   * Check if a resource is selected
   */
  isResourceSelected(resourceName: string): boolean {
    return this.isSelected({ type: "resource", name: resourceName });
  }

  /**
   * Clear all selections
   */
  clear(): void {
    this.selectedItems = [];
    this.lastSelected = null;
  }

  /**
   * Get all selected items
   */
  getSelected(): SelectedItem[] {
    return this.selectedItems;
  }

  /**
   * Get count of selected items
   */
  get count(): number {
    return this.selectedItems.length;
  }

  /**
   * Check if two items are the same
   */
  private isSameItem(a: SelectedItem, b: SelectedItem): boolean {
    if (a.type !== b.type) return false;
    return a.name === b.name;
  }
}

// Export a singleton instance
export const selectionState = new SelectionState();

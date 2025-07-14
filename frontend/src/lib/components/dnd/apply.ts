import type { Active, Over } from "@dnd-kit-svelte/core";

type BaseItem = {
  id: string | number;
  [key: string]: any;
};

/**
 * A generic helper type that extracts all keys from type T
 * whose property values are compatible with an array of T.
 * This allows us to strongly-type the `itemKey`.
 * @example
 * type MyItem = { id: number; title: string; children?: MyItem[] };
 * type Key = ChildKey<MyItem>; // Key is now "children"
 */
type ChildKey<T extends BaseItem> = {
  [P in keyof T]: T[P] extends BaseItem[] | undefined | null ? P : never;
}[keyof T] &
  string;

type FindResult = {
  item: BaseItem;
  parent: BaseItem[];
  index: number;
} | null;

export type DataItem = {
  id: string | number;
  [key: string]: any;
};

/**
 * A straightforward recursive function to find an item's location in a nested structure.
 * It's a "pure" function that doesn't modify any external state.
 * @param items The array to search in.
 * @param id The ID of the item to find.
 * @param itemKey The key for the nested array (e.g., 'children').
 * @returns {FindResult} The found item's location, or null if not found.
 */
function findItemRecursive(
  items: BaseItem[],
  id: string | number,
  itemKey: string, // It accepts a simple string
): FindResult {
  for (let i = 0; i < items.length; i++) {
    const item = items[i];

    // 1. Check if the current item is the one we're looking for.
    if (item && item.id === id) {
      return { item, parent: items, index: i };
    }

    // 2. If not, check if it has a nested array to search within.
    const nestedItems = item[itemKey];
    if (nestedItems && Array.isArray(nestedItems)) {
      const result = findItemRecursive(nestedItems, id, itemKey);
      // 3. If the item was found in a child, immediately return the result up the call stack.
      if (result) {
        return result;
      }
    }
  }

  // 4. If we've searched the entire array and its children without success.
  return null;
}

export function applyDnd<T extends BaseItem>(
  data: T[],
  active: Active,
  over: Over,
  position: "top" | "bottom" | "left" | "right",
  itemKey: ChildKey<T>,
): boolean {
  // Call the helper function twice. The logic is simple and direct.

  const activeLocation = findItemRecursive(data, active.id, itemKey);
  const overLocation = findItemRecursive(data, over.id, itemKey);
  // This check now works perfectly. TypeScript can easily follow the logic
  // and knows that after this point, activeLocation and overLocation are not null.
  if (!activeLocation || !overLocation || active.id === over.id) {
    console.error("Drag and drop failed: active or over item not found, or dropping on itself.");
    return false;
  }

  const { parent: activeParent, index: activeIndex } = activeLocation;
  const { parent: overParent, index: overIndex } = overLocation;

  // 3. EXECUTE THE MOVE
  try {
    // Remove the active item from its original parent array.
    // `splice` returns an array of removed items; we destructure to get the first and only one.
    const [movedItem] = activeParent.splice(activeIndex, 1);
    if (!movedItem) {
      // This should theoretically not happen since we found it, but it's a good safeguard.
      throw new Error("Failed to splice the active item from its parent.");
    }

    // Determine the target index for insertion.
    let finalTargetIndex = overIndex;

    // CRITICAL: Adjust the target index if moving within the same array.
    // If the item was moved from an earlier position to a later position in the same array,
    // the removal of the item will have shifted the indexes of subsequent items down by one.
    if (activeParent === overParent && activeIndex < overIndex) {
      finalTargetIndex = overIndex - 1;
    }

    // Calculate the final insertion index based on the drop position.
    // 'top' or 'left' means insert *before* the 'over' item.
    // 'bottom' or 'right' means insert *after* the 'over' item.
    if (position === "bottom" || position === "right") {
      finalTargetIndex += 1;
    }

    // Insert the moved item into its new parent array at the calculated index.
    overParent.splice(finalTargetIndex, 0, movedItem);

    // Operation was successful.
    return true;
  } catch (error) {
    // Catch any unexpected errors during the splice operations to prevent crashes.
    console.error("An error occurred during the dnd operation:", error);
    // Note: A truly atomic operation would require reverting the first splice on failure.
    // However, given the reliability of `splice`, this try/catch is mainly for edge cases.
    // The in-place nature of the operation prioritizes performance for UI updates.
    return false;
  }
}

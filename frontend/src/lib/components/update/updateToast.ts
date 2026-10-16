import { toast } from "svelte-sonner";
import UpdateToastContent from "./UpdateToastContent.svelte";

/**
 * Prefix of the id of the "update available" toast: the toast on screen reads
 * `update-available-<n>`.
 *
 * Every toast gets a fresh id, so a replacement is a real replacement: the
 * previous toast is dismissed (it plays its exit animation while the new one
 * enters with its own). Creating a toast under an id that is still on screen
 * would make sonner merge the new props into the existing toast -- an
 * in-place update with no exit and no entrance.
 */
export const UPDATE_TOAST_ID_PREFIX = "update-available";

/** One available update the toast announces */
export type UpdateToastData = {
  /** Name of the mod with an available update */
  mod: string;
  /** Latest version of that mod */
  latest: string;
};

// The ids handed out so far. Only the uniqueness of an id matters (see the
// prefix above), never its value, so the counter may live for the session.
let sequence = 0;

/**
 * The update toasts that are on screen right now, by id.
 *
 * Read through sonner instead of holding the id of the last toast in module
 * state: a toast the user just closed is already out of the active list, so
 * a caller can never dismiss a toast that is gone (or twice).
 */
function activeUpdateToastIds(): (string | number)[] {
  return toast
    .getActiveToasts()
    .filter((entry) => String(entry.id).startsWith(UPDATE_TOAST_ID_PREFIX))
    .map((entry) => entry.id);
}

/**
 * Show the "update available" toast for the given updates, replacing the
 * previous toast.
 *
 * The toast never auto-closes (duration = Infinity): it stays until the user
 * closes it, or until a newer set of updates replaces it. The set is the
 * caller's whole knowledge: when a second mod becomes available, the new
 * toast announces both mods instead of dropping the first one.
 *
 * Call it when the set changes: an unchanged set would replay the replacement
 * animation for nothing. An empty list is a no-op (nothing to announce);
 * callers that want to take the toast down use `dismissUpdateToast`.
 */
export function showUpdateToast(updates: UpdateToastData[]): void {
  if (updates.length === 0) return;
  // Dismiss the toast on screen first: it plays its exit animation while the
  // new toast enters under a fresh id
  for (const id of activeUpdateToastIds()) {
    toast.dismiss(id);
  }
  toast.custom(UpdateToastContent, {
    id: `${UPDATE_TOAST_ID_PREFIX}-${++sequence}`,
    duration: Number.POSITIVE_INFINITY,
    componentProps: { updates },
  });
}

/**
 * Close the "update available" toast, if it is on screen.
 *
 * What the close button of the toast content does, for callers that are not
 * inside the toast (the exit animation plays the same way).
 */
export function dismissUpdateToast(): void {
  for (const id of activeUpdateToastIds()) {
    toast.dismiss(id);
  }
}

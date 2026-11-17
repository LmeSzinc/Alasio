// Persistence helpers for the embedded-session switches.
//
// `useLocalStorage` is not reused here: its runes are created at module top
// level, where svelte throws effect_orphan outside component initialization.

/**
 * Reads a JSON value from localStorage.
 *
 * Args:
 *   key: Storage key
 *   fallback: Value returned when the key is absent or unreadable
 */
export function readStored<T>(key: string, fallback: T): T {
  try {
    const item = localStorage.getItem(key);
    return item !== null ? (JSON.parse(item) as T) : fallback;
  } catch {
    return fallback;
  }
}

/**
 * Writes a JSON value to localStorage.
 *
 * Args:
 *   key: Storage key
 *   value: Value to persist
 */
export function writeStored(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage unavailable (e.g. private mode); ignore
  }
}

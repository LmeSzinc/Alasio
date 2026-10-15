/** One commit of the history of a mod (.pack/history.pack of its pack). */
export type HistoryItem = {
  version: string;
  author: string;
  time: number;
  title: string;
  detail: string;
};

/** The history of one mod: its commits, or the error of the backend. */
export type ModHistory = {
  data?: HistoryItem[];
  error?: string;
};

/** The history of every mod of the page (the ModHistory topic), keyed by mod. */
export type ModHistoryData = Record<string, ModHistory>;

/** One mod of the mod list (an entry of the ModList topic). */
export type ModOption = {
  value: string;
  label: string;
};

/** A version that is the sha1 of a packed commit: 40 hex characters. */
const VERSION_SHA1_REGEX = /^[0-9a-f]{40}$/;

/**
 * Version of a mod as it is displayed: the sha1 of a pack (40 hex characters)
 * is shortened to its first 8 characters, every other version (a tag, the
 * version of a wheel) is displayed as it is.
 *
 * Args:
 *   version (str): Version of the mod, e.g. the sha1 of the packed commit
 *
 * Returns:
 *   (str): Version to display, '' for an empty version
 */
export function prettyVersion(version: string): string {
  if (VERSION_SHA1_REGEX.test(version)) {
    return version.slice(0, 8);
  }
  return version;
}

/**
 * Temporary suffix appended to route files during build, e.g.
 * `+page.svelte` -> `+page.svelte.dropped`.
 *
 * The webapp build does not use the drop/restore plugin, but the i18n
 * scanner keeps scanning files with this suffix so stale-key cleanup
 * behaves the same as in the frontend if a build ever leaves dropped
 * files behind.
 */
export const DROPPED_SUFFIX = ".dropped";

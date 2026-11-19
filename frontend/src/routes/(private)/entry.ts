import type { ConfigLike, ConfigTopicLike } from "$lib/components/aside/types";

/**
 * Order of the configurations: the scheduler group first, then the slot inside
 * the group. It is the order the sidebar shows, so "the first configuration"
 * is the first item the user sees.
 *
 * Args:
 *     a (ConfigLike): Left configuration
 *     b (ConfigLike): Right configuration
 *
 * Returns:
 *     number: Negative when a comes first, positive when b comes first
 */
function compareConfigs(a: ConfigLike, b: ConfigLike): number {
  if (a.gid !== b.gid) return a.gid - b.gid;
  return a.iid - b.iid;
}

/**
 * The configuration the app opens when the user enters without one: the first
 * one in sidebar order.
 *
 * The list may legitimately be empty for a while: the backend creates a default
 * configuration per mod on its first start, in a background task, and the
 * ConfigScan topic pushes the new list once it exists. The caller keeps waiting
 * in that case instead of treating it as "no configuration at all".
 *
 * Args:
 *     configs (ConfigTopicLike | undefined): Data of the ConfigScan topic,
 *         undefined until the backend sent its first snapshot
 *
 * Returns:
 *     ConfigLike | undefined: The first configuration, or undefined while the
 *         topic has no data
 */
export function firstConfig(configs: ConfigTopicLike | undefined): ConfigLike | undefined {
  if (!configs) return undefined;

  let first: ConfigLike | undefined;
  for (const config of Object.values(configs)) {
    // The first winner of equal keys is kept, so the pick is deterministic
    // even when two configurations share a position
    if (first === undefined || compareConfigs(config, first) < 0) {
      first = config;
    }
  }
  return first;
}

/**
 * Route of the overview page of a configuration.
 *
 * Args:
 *     name (str): Configuration name
 *
 * Returns:
 *     str: Route of its overview page, name encoded for the URL
 */
export function configOverviewPath(name: string): string {
  return `/config/${encodeURIComponent(name)}/overview`;
}

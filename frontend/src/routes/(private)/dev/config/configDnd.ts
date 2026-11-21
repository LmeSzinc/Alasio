/**
 * State transitions behind the config manager page (ConfigScan.svelte).
 *
 * The page keeps a working copy of the ConfigScan topic in `uiGroups` and
 * updates it optimistically on every drop, before the backend answers. That
 * copy is rebuilt wholesale whenever the topic pushes a new snapshot, so the
 * functions here never rely on the identity of a previous layout: every lookup
 * is by id, because the item ids are database row ids and the group ids are
 * `group-<gid>`. A group id does change when the backend renumbers the groups
 * of a fresh snapshot, which is exactly the window in which a drag can still
 * carry the id of the previous snapshot.
 *
 * A drop is described by `DropTarget`, the ids and types the dnd callbacks
 * carry, rather than by dnd-kit's Active/Over objects, so every transition can
 * be exercised without a browser.
 */
import type { ConfigGroupData } from "./ConfigGroup.svelte";
import type { Config } from "./ConfigItem.svelte";

/** The edge of a droppable that a drop landed on. */
export type DropPosition = "top" | "bottom" | "left" | "right";

/** A finished drop, reduced to what the state transitions need. */
export type DropTarget = {
  activeId: string | number;
  activeType: string | undefined;
  overId: string | number;
  overType: string | undefined;
  position: DropPosition;
};

/**
 * One instruction of the `config_dnd` rpc.
 *
 * The backend sorts every config by (gid, iid) and renumbers the result, so a
 * fractional gid/iid means "between the neighbours of that number", see
 * ScanTable.config_dnd.
 */
export type DndChange = {
  name: string;
  gid: number;
  iid: number;
};

/** Configs and groups of a layout, keyed by the id their dnd data carries. */
export type DndLookup = Map<string | number, Config | ConfigGroupData>;

/**
 * Groups the flat ConfigScan topic data into the layout the page renders.
 *
 * The result is a copy: the page sorts, splices and moves the groups around in
 * place while it updates optimistically, and doing that on the topic data
 * itself would fight the next push of the server.
 *
 * Args:
 *   serverData: The ConfigScan topic data, keyed by config name
 *
 * Returns:
 *   ConfigGroupData[]: groups ordered by gid, items ordered by iid
 */
export function buildGroups(serverData: Record<string, Config> | undefined | null): ConfigGroupData[] {
  if (!serverData) return [];

  const buckets = new Map<number, Config[]>();
  for (const config of Object.values(serverData)) {
    const bucket = buckets.get(config.gid);
    if (bucket) bucket.push(config);
    else buckets.set(config.gid, [config]);
  }

  return [...buckets.entries()]
    .sort(([left], [right]) => left - right)
    .map(([gid, items]) => ({
      id: `group-${gid}`,
      gid,
      items: [...items].sort((left, right) => left.iid - right.iid).map((item) => ({ ...item })),
    }));
}

/**
 * Indexes a layout by the dnd id of its groups and configs.
 *
 * Args:
 *   groups: The layout to index
 *
 * Returns:
 *   DndLookup: group id -> group, config id -> config
 */
export function buildItemMap(groups: ConfigGroupData[]): DndLookup {
  const lookup: DndLookup = new Map();
  for (const group of groups) {
    lookup.set(group.id, group);
    for (const item of group.items) {
      lookup.set(item.id, item);
    }
  }
  return lookup;
}

/**
 * Whether a looked-up value is a group rather than a single config.
 *
 * Args:
 *   value: A config or a group
 *
 * Returns:
 *   bool: true for a group
 */
function isGroup(value: Config | ConfigGroupData): value is ConfigGroupData {
  return "items" in value;
}

/**
 * Whether a looked-up value is a single config.
 *
 * Args:
 *   value: A config or a group
 *
 * Returns:
 *   bool: true for a config
 */
function isConfig(value: Config | ConfigGroupData): value is Config {
  return "iid" in value;
}

/**
 * Translates a drop into the instructions of the `config_dnd` rpc.
 *
 * The instructions are positions relative to the *current* target, and the
 * backend resolves them against its own state: it is the backend that decides
 * which rows move and how the groups are renumbered. Nothing is returned for a
 * drop that would not change the layout (dropping a config onto itself,
 * dropping a lone config onto the group it already fills, dropping onto an id
 * that the last server snapshot no longer has).
 *
 * Args:
 *   drop: The finished drop
 *   lookup: Configs and groups of the layout the user was dragging in
 *
 * Returns:
 *   DndChange[]: instructions to send, empty when nothing has to change
 */
export function planDndChanges(drop: DropTarget, lookup: DndLookup): DndChange[] {
  if (!drop.activeType || !drop.overType) return [];
  if (drop.activeId === drop.overId) return [];

  const active = lookup.get(drop.activeId);
  const over = lookup.get(drop.overId);
  if (!active || !over) return [];

  if (drop.activeType === "item") {
    const activeConfig = active;
    if (!isConfig(activeConfig)) return [];

    if (drop.overType === "item") {
      if (!isConfig(over)) return [];
      const overConfig = over;
      // Dropping a config onto itself is a no-op, the ids are compared above
      // for the general case and here for two ids of different types.
      if (activeConfig.id === overConfig.id) return [];
      if (drop.position === "top") {
        return [{ name: activeConfig.name, gid: overConfig.gid, iid: overConfig.iid - 0.01 }];
      }
      if (drop.position === "bottom") {
        return [{ name: activeConfig.name, gid: overConfig.gid, iid: overConfig.iid + 0.01 }];
      }
      return [];
    }

    if (drop.overType === "group") {
      if (!isGroup(over)) return [];
      const overGroup = over;
      // The config already fills the group on its own: there is no new group
      // to create and no neighbour to move away from.
      if (activeConfig.gid === overGroup.gid && overGroup.items.length === 1) return [];
      if (drop.position === "top") {
        return [{ name: activeConfig.name, gid: overGroup.gid - 0.01, iid: 1 }];
      }
      if (drop.position === "bottom") {
        return [{ name: activeConfig.name, gid: overGroup.gid + 0.01, iid: 1 }];
      }
      return [];
    }

    return [];
  }

  if (drop.activeType === "group" && drop.overType === "group") {
    if (!isGroup(active) || !isGroup(over)) return [];
    const activeGroup = active;
    const overGroup = over;
    if (activeGroup.id === overGroup.id) return [];
    // An empty group has nothing to move; the page never renders one, this
    // guard keeps a stale layout from sending an empty instruction list.
    if (activeGroup.items.length === 0) return [];

    const targetGid = drop.position === "top" ? overGroup.gid - 0.01 : overGroup.gid + 0.01;
    return activeGroup.items.map((item, index) => ({
      name: item.name,
      gid: targetGid,
      iid: index + 1,
    }));
  }

  return [];
}

/**
 * Applies a drop to the layout the user sees, without touching the backend.
 *
 * The transitions mirror what `planDndChanges` asks the backend for, so the
 * page does not jump when the answer (a new snapshot) arrives. The function is
 * total: a drop whose target is no longer in the layout (a snapshot landed
 * between hovering and dropping) changes nothing and never loses the dragged
 * config. Every config of the input is present exactly once in the output, and
 * empty groups are dropped.
 *
 * The server-owned `gid`/`iid` of the configs are left alone: they are the
 * numbers the next `config_dnd` instruction is computed from, and they only
 * become the optimistic layout after the backend answers with a new snapshot.
 * The `gid` of a group created here is a placeholder for the same reason; the
 * page displays the position of the group instead.
 *
 * Args:
 *   groups: The layout the user is dragging in
 *   drop: The finished drop
 *
 * Returns:
 *   ConfigGroupData[]: the optimistically updated layout, the input array
 *       itself when the drop changes nothing
 */
export function applyOptimisticDrop(groups: ConfigGroupData[], drop: DropTarget): ConfigGroupData[] {
  if (!drop.activeType || !drop.overType) return groups;
  if (drop.activeId === drop.overId) return groups;

  if (drop.activeType === "item" && drop.overType === "group") {
    return dropConfigOnGroup(groups, drop);
  }
  if (drop.activeType === "item" && drop.overType === "item") {
    return moveConfig(groups, drop);
  }
  if (drop.activeType === "group" && drop.overType === "group") {
    return moveGroup(groups, drop);
  }
  return groups;
}

/**
 * Moves a config into a group of its own, placed before or after the group it
 * was dropped on.
 *
 * Args:
 *   groups: The layout to update
 *   drop: A drop of a config on a group
 *
 * Returns:
 *   ConfigGroupData[]: the updated layout, or the input when the drop is a
 *       no-op or its target is gone
 */
function dropConfigOnGroup(groups: ConfigGroupData[], drop: DropTarget): ConfigGroupData[] {
  const source = findConfig(groups, drop.activeId);
  if (!source) return groups;

  const targetIndex = groups.findIndex((group) => group.id === drop.overId);
  if (targetIndex === -1) return groups;
  const overGroup = groups[targetIndex];

  // Dropping a lone config onto the group it already fills changes nothing.
  if (overGroup.id === source.group.id && source.group.items.length === 1) return groups;

  const next = groups.map((group, index) =>
    index === source.groupIndex ? { ...group, items: group.items.filter((item) => item.id !== source.item.id) } : group,
  );

  const newGroup: ConfigGroupData = {
    // The id is unique until the backend renumbers the groups, and the keyed
    // each block needs it to be different from every server group id.
    id: `group-new-${source.item.id}`,
    gid: overGroup.gid,
    items: [source.item],
  };

  // The insertion point is computed on the array before the emptied source
  // group is removed: filtering afterwards keeps the relative order of the
  // remaining groups, so before/after the target stays before/after it.
  const insertionIndex = drop.position === "top" ? targetIndex : targetIndex + 1;
  const inserted = [...next.slice(0, insertionIndex), newGroup, ...next.slice(insertionIndex)];
  return inserted.filter((group) => group.items.length > 0);
}

/**
 * Moves a config next to another config, into that config's group.
 *
 * Args:
 *   groups: The layout to update
 *   drop: A drop of a config on a config
 *
 * Returns:
 *   ConfigGroupData[]: the updated layout, or the input when the drop is a
 *       no-op or one of the two configs is gone
 */
function moveConfig(groups: ConfigGroupData[], drop: DropTarget): ConfigGroupData[] {
  const next = groups.map((group) => ({ ...group, items: [...group.items] }));
  const source = findConfig(next, drop.activeId);
  const target = findConfig(next, drop.overId);
  if (!source || !target) return groups;
  if (source.item.id === target.item.id) return groups;

  const [moved] = source.group.items.splice(source.index, 1);
  // The target index is read after the removal, so moving a config downwards
  // within its own group lands on the edge of the target that was asked for.
  const targetIndex = target.group.items.indexOf(target.item);
  const insertionIndex = drop.position === "bottom" || drop.position === "right" ? targetIndex + 1 : targetIndex;
  target.group.items.splice(insertionIndex, 0, moved);

  return next.filter((group) => group.items.length > 0);
}

/**
 * Moves a whole group before or after another group.
 *
 * Args:
 *   groups: The layout to update
 *   drop: A drop of a group on a group
 *
 * Returns:
 *   ConfigGroupData[]: the updated layout, or the input when the drop is a
 *       no-op or one of the two groups is gone
 */
function moveGroup(groups: ConfigGroupData[], drop: DropTarget): ConfigGroupData[] {
  const sourceIndex = groups.findIndex((group) => group.id === drop.activeId);
  const targetIndex = groups.findIndex((group) => group.id === drop.overId);
  if (sourceIndex === -1 || targetIndex === -1 || sourceIndex === targetIndex) return groups;

  const next = [...groups];
  const [moved] = next.splice(sourceIndex, 1);
  const overIndex = next.findIndex((group) => group.id === drop.overId);
  const insertionIndex = drop.position === "top" ? overIndex : overIndex + 1;
  next.splice(insertionIndex, 0, moved);
  return next;
}

/**
 * Finds a config in a layout.
 *
 * Args:
 *   groups: The layout to search
 *   id: The dnd id of the config
 *
 * Returns:
 *   {item, group, groupIndex, index} | null: the config, the group holding
 *       it, the index of that group in the layout and the index of the config
 *       in the group
 */
function findConfig(
  groups: ConfigGroupData[],
  id: string | number,
): { item: Config; group: ConfigGroupData; groupIndex: number; index: number } | null {
  for (const [groupIndex, group] of groups.entries()) {
    const index = group.items.findIndex((item) => item.id === id);
    if (index !== -1) {
      return { item: group.items[index], group, groupIndex, index };
    }
  }
  return null;
}

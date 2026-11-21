/**
 * Tests for the state transitions of the config manager page (configDnd.ts)
 *
 * Two things are covered here: the instructions the page sends to the backend
 * (`config_dnd`), and the optimistic update it applies to the layout before
 * the answer arrives. The optimistic update must never lose or duplicate a
 * config, whatever the drop and whatever the state of the layout: a snapshot
 * can land between hovering and dropping, and the drop then carries the id of
 * the previous snapshot.
 */
import { beforeEach, describe, expect, it } from "vitest";
import type { ConfigGroupData } from "./ConfigGroup.svelte";
import type { Config } from "./ConfigItem.svelte";
import { type DropTarget, applyOptimisticDrop, buildGroups, buildItemMap, planDndChanges } from "./configDnd";

/** Ids of the fixtures, unique over a test so lookups cannot collide. */
let nextId = 1;

beforeEach(() => {
  nextId = 1;
});

/** A config as the ConfigScan topic carries it. */
function config(name: string, gid: number, iid: number): Config {
  return { id: nextId++, name, mod: "TestMod", gid, iid, extra: "" };
}

/**
 * A layout of the shapes the page renders, with group ids and gids numbered
 * from 1 as the backend renumbers them.
 */
function layout(...groups: Config[][]): ConfigGroupData[] {
  return groups.map((items, index) => ({
    id: `group-${index + 1}`,
    gid: index + 1,
    items,
  }));
}

/** The topic data of a layout: configs keyed by name, as the server sends it. */
function topicData(groups: ConfigGroupData[]): Record<string, Config> {
  return Object.fromEntries(groups.flatMap((group) => group.items.map((item) => [item.name, item])));
}

/** Item names of a layout, group by group. */
function names(groups: ConfigGroupData[]): string[][] {
  return groups.map((group) => group.items.map((item) => item.name));
}

/** Every config id of a layout, in render order. */
function ids(groups: ConfigGroupData[]): number[] {
  return groups.flatMap((group) => group.items.map((item) => item.id));
}

/** A drop of one config or group onto another. */
function drop(
  active: Config | ConfigGroupData,
  over: Config | ConfigGroupData,
  activeType: "item" | "group",
  overType: "item" | "group",
  position: DropTarget["position"] = "top",
): DropTarget {
  return {
    activeId: active.id,
    activeType,
    overId: over.id,
    overType,
    position,
  };
}

describe("TestBuildGroups", () => {
  it("orders groups by gid and the configs of a group by iid", () => {
    // The topic data is keyed by name, its insertion order means nothing
    const data = topicData(layout([config("b2", 2, 2), config("b1", 2, 1)], [config("c1", 3, 1)]));
    const extra = config("a2", 1, 2);
    const first = config("a1", 1, 1);
    const shuffled = { b2: data.b2, a2: extra, c1: data.c1, b1: data.b1, a1: first };

    expect(names(buildGroups(shuffled))).toEqual([["a1", "a2"], ["b1", "b2"], ["c1"]]);
  });

  it("returns nothing without data", () => {
    expect(buildGroups(undefined)).toEqual([]);
    expect(buildGroups(null)).toEqual([]);
    expect(buildGroups({})).toEqual([]);
  });

  it("copies the configs and their arrays", () => {
    const first = config("a1", 1, 1);
    const groups = buildGroups({ a1: first });

    groups[0].items[0].name = "renamed";
    groups[0].items.push(config("a2", 1, 2));

    // The page sorts and splices its copy, the topic data must not follow
    expect(first.name).toBe("a1");
    expect(groups).toHaveLength(1);
  });
});

describe("TestBuildItemMap", () => {
  it("indexes groups and configs by their dnd id", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const groups = layout([a], [b]);
    const lookup = buildItemMap(groups);

    expect(lookup.get("group-1")).toBe(groups[0]);
    expect(lookup.get("group-2")).toBe(groups[1]);
    expect(lookup.get(a.id)).toBe(a);
    expect(lookup.get(b.id)).toBe(b);
    expect(lookup.get("group-3")).toBeUndefined();
  });
});

describe("TestPlanDndChanges", () => {
  it("places a config before or after the target config", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const c = config("c", 2, 2);
    const groups = layout([a], [b, c]);
    const lookup = buildItemMap(groups);

    expect(planDndChanges(drop(a, c, "item", "item", "top"), lookup)).toEqual([{ name: "a", gid: 2, iid: 1.99 }]);
    expect(planDndChanges(drop(a, c, "item", "item", "bottom"), lookup)).toEqual([{ name: "a", gid: 2, iid: 2.01 }]);
  });

  it("ignores a config dropped on itself", () => {
    const a = config("a", 1, 1);
    const lookup = buildItemMap(layout([a]));

    expect(planDndChanges(drop(a, a, "item", "item", "top"), lookup)).toEqual([]);
  });

  it("creates a group before or after the target group", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const lookup = buildItemMap(layout([a], [b]));

    expect(planDndChanges(drop(a, { id: "group-2", gid: 2, items: [b] }, "item", "group", "top"), lookup)).toEqual([
      { name: "a", gid: 1.99, iid: 1 },
    ]);
    expect(planDndChanges(drop(a, { id: "group-2", gid: 2, items: [b] }, "item", "group", "bottom"), lookup)).toEqual([
      { name: "a", gid: 2.01, iid: 1 },
    ]);
  });

  it("does not split a config from the group it fills alone", () => {
    const a = config("a", 1, 1);
    const lookup = buildItemMap(layout([a]));

    expect(planDndChanges(drop(a, { id: "group-1", gid: 1, items: [a] }, "item", "group", "top"), lookup)).toEqual([]);
    expect(planDndChanges(drop(a, { id: "group-1", gid: 1, items: [a] }, "item", "group", "bottom"), lookup)).toEqual(
      [],
    );
  });

  it("splits a config out of its own group when more configs stay behind", () => {
    const a = config("a", 2, 1);
    const b = config("b", 2, 2);
    const lookup = buildItemMap(layout([config("x", 1, 1)], [a, b]));

    expect(
      planDndChanges(drop(a, { id: "group-2", gid: 2, items: [a, b] }, "item", "group", "bottom"), lookup),
    ).toEqual([{ name: "a", gid: 2.01, iid: 1 }]);
  });

  it("moves every config of a group before or after the target group", () => {
    const a1 = config("a1", 1, 1);
    const a2 = config("a2", 1, 2);
    const b = config("b", 2, 1);
    const c = config("c", 3, 1);
    const groups = layout([a1, a2], [b], [c]);
    const lookup = buildItemMap(groups);
    const active = groups[0];
    const over = groups[2];

    expect(planDndChanges(drop(active, over, "group", "group", "top"), lookup)).toEqual([
      { name: "a1", gid: 2.99, iid: 1 },
      { name: "a2", gid: 2.99, iid: 2 },
    ]);
    expect(planDndChanges(drop(active, over, "group", "group", "bottom"), lookup)).toEqual([
      { name: "a1", gid: 3.01, iid: 1 },
      { name: "a2", gid: 3.01, iid: 2 },
    ]);
  });

  it("ignores a group dropped on itself", () => {
    const a = config("a", 1, 1);
    const groups = layout([a]);
    const lookup = buildItemMap(groups);

    expect(planDndChanges(drop(groups[0], groups[0], "group", "group", "top"), lookup)).toEqual([]);
  });

  it("ignores a drop whose target is gone", () => {
    const a = config("a", 1, 1);
    const lookup = buildItemMap(layout([a]));
    const staleGroup = { id: "group-9", gid: 9, items: [] } as ConfigGroupData;
    const staleConfig = { ...config("z", 9, 1), id: 999 };

    // The last snapshot renumbered the groups: an id of the previous one no
    // longer resolves, and the backend must not be told to move anything
    expect(planDndChanges(drop(a, staleGroup, "item", "group", "top"), lookup)).toEqual([]);
    expect(planDndChanges({ ...drop(a, staleConfig, "item", "item", "top"), activeId: 12345 }, lookup)).toEqual([]);
    expect(planDndChanges({ ...drop(a, staleGroup, "item", "group", "top"), activeId: 12345 }, lookup)).toEqual([]);
  });

  it("ignores an unsupported combination of types", () => {
    const a = config("a", 1, 1);
    const groups = layout([a], [config("b", 2, 1)]);
    const lookup = buildItemMap(groups);

    // The dnd rules only accept an item on an item or a group, and a group on
    // a group; the planner is defensive about anything else
    expect(planDndChanges(drop(groups[0], a, "group", "item", "top"), lookup)).toEqual([]);
    expect(planDndChanges({ ...drop(a, groups[1], "item", "group", "top"), activeType: undefined }, lookup)).toEqual(
      [],
    );
    expect(planDndChanges({ ...drop(a, groups[1], "item", "group", "top"), overType: undefined }, lookup)).toEqual([]);
  });

  it("ignores the horizontal edges of a vertical list", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const lookup = buildItemMap(layout([a], [b]));

    expect(planDndChanges(drop(a, b, "item", "item", "left"), lookup)).toEqual([]);
    expect(planDndChanges(drop(a, b, "item", "item", "right"), lookup)).toEqual([]);
  });
});

describe("TestApplyOptimisticDrop", () => {
  it("drops a config next to a config of another group", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const c = config("c", 2, 2);
    const groups = layout([a], [b, c]);

    expect(names(applyOptimisticDrop(groups, drop(a, b, "item", "item", "top")))).toEqual([["a", "b", "c"]]);
    expect(names(applyOptimisticDrop(groups, drop(a, c, "item", "item", "bottom")))).toEqual([["b", "c", "a"]]);
  });

  it("moves a config down inside its own group", () => {
    const a = config("a", 1, 1);
    const b = config("b", 1, 2);
    const c = config("c", 1, 3);
    const groups = layout([a, b, c]);

    expect(names(applyOptimisticDrop(groups, drop(a, c, "item", "item", "bottom")))).toEqual([["b", "c", "a"]]);
    expect(names(applyOptimisticDrop(groups, drop(a, b, "item", "item", "bottom")))).toEqual([["b", "a", "c"]]);
  });

  it("moves a config up inside its own group", () => {
    const a = config("a", 1, 1);
    const b = config("b", 1, 2);
    const c = config("c", 1, 3);
    const groups = layout([a, b, c]);

    expect(names(applyOptimisticDrop(groups, drop(c, a, "item", "item", "top")))).toEqual([["c", "a", "b"]]);
    expect(names(applyOptimisticDrop(groups, drop(c, b, "item", "item", "top")))).toEqual([["a", "c", "b"]]);
  });

  it("gives a config dropped on a group a group of its own", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const c = config("c", 3, 1);
    const groups = layout([a], [b], [c]);

    const before = applyOptimisticDrop(groups, drop(a, groups[1], "item", "group", "top"));
    expect(names(before)).toEqual([["a"], ["b"], ["c"]]);
    expect(before[0].id).toBe(`group-new-${a.id}`);

    const after = applyOptimisticDrop(groups, drop(a, groups[1], "item", "group", "bottom"));
    expect(names(after)).toEqual([["b"], ["a"], ["c"]]);
    expect(after[1].id).toBe(`group-new-${a.id}`);
  });

  it("removes the group a config was the last of", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const groups = layout([a], [b]);

    // The config gets a group of its own next to the target, and the group it
    // leaves behind is gone
    expect(names(applyOptimisticDrop(groups, drop(a, groups[1], "item", "group", "top")))).toEqual([["a"], ["b"]]);
    expect(applyOptimisticDrop(groups, drop(a, groups[1], "item", "group", "top"))).toHaveLength(2);
  });

  it("keeps a config dropped on the group it fills alone", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const groups = layout([a], [b]);

    expect(applyOptimisticDrop(groups, drop(a, groups[0], "item", "group", "top"))).toEqual(groups);
    expect(applyOptimisticDrop(groups, drop(a, groups[0], "item", "group", "bottom"))).toEqual(groups);
  });

  it("keeps the config when the target group is gone", () => {
    // A snapshot landed between hovering and dropping: the drop carries the
    // group id of the previous snapshot, which the current layout does not have
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const groups = layout([a], [b]);
    const staleGroup = { id: "group-9", gid: 9, items: [] } as ConfigGroupData;

    const result = applyOptimisticDrop(groups, drop(a, staleGroup, "item", "group", "top"));

    expect(result).toEqual(groups);
    expect(ids(result)).toEqual([a.id, b.id]);
  });

  it("keeps the layout when the dragged config is gone", () => {
    const a = config("a", 1, 1);
    const groups = layout([a]);
    const staleConfig = { ...config("z", 2, 1), id: 999 };

    expect(applyOptimisticDrop(groups, drop(staleConfig, a, "item", "item", "top"))).toEqual(groups);
  });

  it("moves a whole group before or after another group", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const c = config("c", 3, 1);
    const groups = layout([a], [b], [c]);

    expect(names(applyOptimisticDrop(groups, drop(groups[0], groups[2], "group", "group", "top")))).toEqual([
      ["b"],
      ["a"],
      ["c"],
    ]);
    expect(names(applyOptimisticDrop(groups, drop(groups[0], groups[1], "group", "group", "bottom")))).toEqual([
      ["b"],
      ["a"],
      ["c"],
    ]);
  });

  it("leaves the server positions of the configs alone", () => {
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const groups = layout([a], [b]);

    const result = applyOptimisticDrop(groups, drop(a, groups[1], "item", "group", "top"));

    // The next instruction is computed from these numbers, they only change
    // when the backend answers with a new snapshot
    expect(result[0].items[0].gid).toBe(1);
    expect(result[0].items[0].iid).toBe(1);
  });

  it("round trips a config out of and back into its group", () => {
    // The reported scenario: drag a config from group 1 to between the groups,
    // then drag the same config back
    const a = config("a", 1, 1);
    const b = config("b", 2, 1);
    const c = config("c", 3, 1);
    const groups = layout([a], [b], [c]);

    const moved = applyOptimisticDrop(groups, drop(a, groups[1], "item", "group", "bottom"));
    expect(names(moved)).toEqual([["b"], ["a"], ["c"]]);

    const back = applyOptimisticDrop(moved, drop(moved[1].items[0], moved[0], "item", "group", "top"));
    expect(names(back)).toEqual([["a"], ["b"], ["c"]]);
    expect(ids(back)).toEqual([a.id, b.id, c.id]);
  });
});

describe("TestApplyOptimisticDropInvariants", () => {
  /** Deterministic PRNG (a plain LCG) so a failing case can be re-run. */
  function random(seed: number) {
    let state = seed;
    return () => {
      state = (state * 1664525 + 1013904223) % 4294967296;
      return state / 4294967296;
    };
  }

  /** Asserts the layout holds every config of `expected` exactly once. */
  function expectExactlyOnce(groups: ConfigGroupData[], expected: number[]) {
    const seen = ids(groups);
    expect([...seen].sort((left, right) => left - right)).toEqual([...expected].sort((left, right) => left - right));
    expect(new Set(seen).size).toBe(seen.length);
  }

  it("keeps every config exactly once over random drops", () => {
    const rand = random(20261121);

    for (let round = 0; round < 300; round++) {
      // A random layout, built the way the topic data is: configs first, the
      // groups are whatever gid the configs carry
      const groupCount = 1 + Math.floor(rand() * 4);
      const data: Record<string, Config> = {};
      for (let gid = 1; gid <= groupCount; gid++) {
        const count = 1 + Math.floor(rand() * 3);
        for (let iid = 1; iid <= count; iid++) {
          const name = `g${gid}c${iid}`;
          data[name] = config(name, gid, iid);
        }
      }
      const groups = buildGroups(data);
      const allIds = ids(groups);
      const configs = groups.flatMap((group) => group.items);

      // Pick a random drop. Sometimes the target id belongs to a snapshot that
      // is gone, as when a push lands between hovering and dropping.
      const active = configs[Math.floor(rand() * configs.length)];
      const mode = rand();
      let target: DropTarget;
      if (mode < 0.45) {
        const over = configs[Math.floor(rand() * configs.length)];
        target = drop(active, over, "item", "item", rand() < 0.5 ? "top" : "bottom");
      } else if (mode < 0.8) {
        const over = groups[Math.floor(rand() * groups.length)];
        target = drop(active, over, "item", "group", rand() < 0.5 ? "top" : "bottom");
      } else if (mode < 0.95) {
        const activeGroup = groups[Math.floor(rand() * groups.length)];
        const overGroup = groups[Math.floor(rand() * groups.length)];
        target = drop(activeGroup, overGroup, "group", "group", rand() < 0.5 ? "top" : "bottom");
      } else {
        target = drop(active, { id: "group-999", gid: 999, items: [] } as ConfigGroupData, "item", "group", "top");
      }

      const result = applyOptimisticDrop(groups, target);

      expect(result.every((group) => group.items.length > 0)).toBe(true);
      expectExactlyOnce(result, allIds);
      // The drop can create at most one group, and only for an item on a group
      expect(result.length).toBeLessThanOrEqual(groups.length + 1);
      // Whatever the drop, the layout keeps the configs' server positions
      for (const group of result) {
        for (const item of group.items) {
          expect(item.gid).toBe(data[item.name].gid);
          expect(item.iid).toBe(data[item.name].iid);
        }
      }
    }
  });

  it("keeps every config exactly once when drops are applied back to back", () => {
    const rand = random(7);

    for (let round = 0; round < 150; round++) {
      const a = config("a", 1, 1);
      const b = config("b", 2, 1);
      const c = config("c", 2, 2);
      const d = config("d", 3, 1);
      const groups = layout([a], [b, c], [d]);
      const allIds = ids(groups);

      // A drop the user could make right after another one, before the answer
      // of the first reached the page
      const options: DropTarget[] = [
        drop(a, groups[1], "item", "group", rand() < 0.5 ? "top" : "bottom"),
        drop(d, groups[1], "item", "group", rand() < 0.5 ? "top" : "bottom"),
        drop(a, c, "item", "item", rand() < 0.5 ? "top" : "bottom"),
        drop(groups[0], groups[2], "group", "group", rand() < 0.5 ? "top" : "bottom"),
      ];
      const first = applyOptimisticDrop(groups, options[0]);
      const second = applyOptimisticDrop(first, options[1 + Math.floor(rand() * 3)]);

      expectExactlyOnce(second, allIds);
      expect(second.every((group) => group.items.length > 0)).toBe(true);
    }
  });
});

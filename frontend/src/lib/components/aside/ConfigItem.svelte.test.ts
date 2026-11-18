/**
 * Tests for the sidebar config item (ConfigItem.svelte)
 *
 * The item draws the worker state in its bottom right corner. While the startup
 * flow owns the backend, the first full update (check, download, apply) is
 * marked in the top right corner instead, so the update stays visible without
 * taking the state badge over.
 *
 * The file name carries `.svelte.test.ts` so the plugin compiles it as a svelte
 * module: the props are reactive state the test rewrites between assertions.
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import ConfigItem from "./ConfigItem.svelte";
import type { ConfigLike, RestartTopicLike, WORKER_STATE } from "./types";

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

const CONFIG: ConfigLike = { id: 1, name: "cfg_a", mod: "TestMod", gid: 1, iid: 1 };

type Flow = RestartTopicLike["flow"];

/**
 * Mounts the item over reactive props, so a test can change the worker state or
 * the flow of the running component.
 */
async function mountItem(state: WORKER_STATE, flow?: Flow) {
  const props = $state<{ config: ConfigLike; state: WORKER_STATE; flow?: Flow }>({
    config: CONFIG,
    state,
    flow,
  });
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(ConfigItem, { target, props }));
  await flushEffects();
  return { target, props };
}

/** The aria-label of the state badge (the icon below the mod icon), or null */
function badge(target: HTMLElement): string | null {
  const icon = target.querySelector('[data-slot="state-badge"] svg[aria-label]');
  return icon?.getAttribute("aria-label") ?? null;
}

/** The aria-label of the startup update marker, or null when it is not drawn */
function updateMarker(target: HTMLElement): string | null {
  const icon = target.querySelector('[data-slot="startup-update"] svg[aria-label]');
  return icon?.getAttribute("aria-label") ?? null;
}

/** Unmounts the component of one assertion (every case mounts its own) */
function close(component: any) {
  unmount(component);
  mounted = mounted.filter((handle) => handle !== component);
  document.body.innerHTML = "";
}

afterEach(() => {
  // Unmount every component the test mounted, even on assertion failure: a
  // leaked component keeps its effects alive.
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
});

describe("TestConfigItem", () => {
  it("draws the worker state when no flow owns the backend", async () => {
    for (const [state, label] of [
      ["running", "Running"],
      ["resuming", "Resuming"],
      ["restarting", "Restarting"],
      ["updating", "Updating"],
    ] as const) {
      const { target } = await mountItem(state);
      expect(badge(target)).toBe(label);
      expect(updateMarker(target)).toBeNull();
      close(mounted[mounted.length - 1]);
    }
  });

  it("marks the startup update next to the worker state", async () => {
    // the update of the process start gets its own corner: a config may be idle
    // (cold start) or waiting for the resume queue, its state badge stays
    for (const [state, label] of [
      ["idle", null],
      ["resuming", "Resuming"],
      ["error", "Error"],
    ] as const) {
      const { target } = await mountItem(state, "startup");
      expect(badge(target)).toBe(label);
      expect(updateMarker(target)).toBe("Updating");
      close(mounted[mounted.length - 1]);
    }
  });

  it("does not repeat the updating marker of a config that is updating", async () => {
    // the backend parks a config in "updating" for its own update flow: the
    // state badge already says it, a second arrow would only duplicate it
    const { target } = await mountItem("updating", "startup");
    expect(badge(target)).toBe("Updating");
    expect(target.querySelector('[data-slot="startup-update"]')).toBeNull();
  });

  it("ignores the other flows", async () => {
    for (const flow of ["restart", "update", "daily", "init", ""] as const) {
      const { target } = await mountItem("restarting", flow);
      expect(updateMarker(target)).toBeNull();
      close(mounted[mounted.length - 1]);
    }
  });

  it("drops the marker when the startup flow ends", async () => {
    const { target, props } = await mountItem("resuming", "startup");
    expect(updateMarker(target)).toBe("Updating");

    // the startup flow released the backend: the resume queue owns the config
    props.flow = "";
    await flushEffects();

    expect(updateMarker(target)).toBeNull();
    expect(badge(target)).toBe("Resuming");
  });
});

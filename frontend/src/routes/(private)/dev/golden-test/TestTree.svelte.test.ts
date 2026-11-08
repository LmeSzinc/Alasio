import { mount, unmount } from "svelte";
import { type Mock, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import TestTree from "./TestTree.svelte";
import type { GoldenTestEntry } from "./types";

/**
 * The tree of the review page.
 *
 * The backend sends one folder level at a time: opening a folder subscribes it,
 * closing it releases the folder and everything below it. The component keeps
 * only which folders are open, the entries come from the topic.
 */

function entry(name: string, path: string, isDir: boolean): GoldenTestEntry {
  return { name, path, is_dir: isDir };
}

function makeDirs(): Record<string, GoldenTestEntry[]> {
  return {
    "": [entry("map", "map", true), entry("test_screenshot.py", "test_screenshot.py", false)],
  };
}

function labels(target: HTMLElement): string[] {
  return Array.from(target.querySelectorAll("button")).map((button) => button.textContent?.trim() ?? "");
}

function buttonByText(target: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(target.querySelectorAll("button")).find((element) => element.textContent?.includes(text));
  if (button === undefined) throw new Error(`no button "${text}", have: ${labels(target).join(", ")}`);
  return button;
}

describe("TestTree", () => {
  let target: HTMLElement;
  let onSelect: Mock<(path: string) => void>;
  let onSubFolder: Mock<(path: string) => void>;
  let onUnsubFolder: Mock<(path: string) => void>;
  let component: Record<string, unknown> | undefined;

  beforeEach(() => {
    target = document.createElement("div");
    document.body.appendChild(target);
    onSelect = vi.fn<(path: string) => void>();
    onSubFolder = vi.fn<(path: string) => void>();
    onUnsubFolder = vi.fn<(path: string) => void>();
  });

  afterEach(() => {
    if (component !== undefined) unmount(component);
    target.remove();
  });

  async function render(
    directoryTree: Record<string, GoldenTestEntry[]>,
    selected = "",
  ): Promise<{ dirs: Record<string, GoldenTestEntry[]> }> {
    // The page hands the tree a $state record of the topic, so the tree sees
    // the writes that the backend pushes make to it
    const dirs = $state(directoryTree);
    component = mount(TestTree, {
      target,
      props: { dirs, selected, onSelect, onSubFolder, onUnsubFolder },
    });
    await flushEffects();
    return { dirs };
  }

  it("renders the root level", async () => {
    await render(makeDirs());

    expect(labels(target)).toEqual(["map", "test_screenshot.py"]);
  });

  it("selects a file", async () => {
    await render(makeDirs());

    buttonByText(target, "test_screenshot.py").click();

    expect(onSelect).toHaveBeenCalledWith("test_screenshot.py");
    expect(onSubFolder).not.toHaveBeenCalled();
  });

  it("subscribes a folder when it is opened and shows its children", async () => {
    const { dirs } = await render(makeDirs());

    buttonByText(target, "map").click();
    await flushEffects();
    expect(onSubFolder).toHaveBeenCalledWith("map");

    // The backend answers with the level
    dirs["map"] = [entry("fleet", "map/fleet", true), entry("test_fleet.py", "map/test_fleet.py", false)];
    await flushEffects();

    expect(labels(target)).toEqual(["map", "fleet", "test_fleet.py", "test_screenshot.py"]);
  });

  it("releases a folder and the folders below it when it is closed", async () => {
    const { dirs } = await render(makeDirs());
    dirs["map"] = [entry("fleet", "map/fleet", true)];
    await flushEffects();

    buttonByText(target, "map").click();
    await flushEffects();
    buttonByText(target, "fleet").click();
    await flushEffects();
    expect(onSubFolder).toHaveBeenLastCalledWith("map/fleet");

    onSubFolder.mockClear();
    buttonByText(target, "map").click();
    await flushEffects();

    // Closing the parent releases the parent only: the backend drops the
    // folders below it as well
    expect(onUnsubFolder).toHaveBeenCalledTimes(1);
    expect(onUnsubFolder).toHaveBeenCalledWith("map");
    // The children of the released folder are gone from the view
    expect(labels(target)).toEqual(["map", "test_screenshot.py"]);
  });

  it("subscribes a folder again when it is opened again", async () => {
    await render(makeDirs());

    buttonByText(target, "map").click();
    await flushEffects();
    buttonByText(target, "map").click();
    await flushEffects();
    onSubFolder.mockClear();
    buttonByText(target, "map").click();
    await flushEffects();

    expect(onSubFolder).toHaveBeenCalledWith("map");
  });

  it("marks the selected file", async () => {
    await render(makeDirs(), "test_screenshot.py");

    const selected = buttonByText(target, "test_screenshot.py");
    // The rows share the highlight of the dev nav: the current one is primary
    // and bold, every row hovers like a nav entry
    expect(selected.className).toContain("text-primary");
    expect(selected.className).toContain("font-semibold");
    expect(buttonByText(target, "map").className).toContain("font-medium");
    expect(buttonByText(target, "map").className).toContain("hover:bg-accent");
  });
});

/**
 * Tests for the rendering of one translated text.
 *
 * A text of multiple lines is one block of rows, not a row of siblings: the
 * callers put it in a flex row next to a button or an input, where siblings
 * would be laid out side by side instead of one line under the other.
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import I18nText from "./I18nText.svelte";

let mounted: any[] = [];
let target: HTMLElement;

async function render(text: string | string[] | undefined | null, className?: string): Promise<HTMLElement> {
  target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(I18nText, { target, props: { text, class: className } }));
  await flushEffects();
  return target;
}

function rows(): string[] {
  return [...target.querySelectorAll("p")].map((el) => el.textContent ?? "");
}

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
});

describe("TestI18nText", () => {
  it("renders a text of one line as one row", async () => {
    await render("The mode");

    expect(rows()).toEqual(["The mode"]);
  });

  it("renders a text of multiple lines as one row per line", async () => {
    await render(["The mode", "of the task"]);

    expect(rows()).toEqual(["The mode", "of the task"]);
  });

  it("stacks the rows in one block, so a flex row parent lays out the text as a whole", async () => {
    await render(["The mode", "of the task"]);

    const block = target.firstElementChild!;
    expect([...block.children].map((el) => el.tagName)).toEqual(["P", "P"]);
    expect(target.children).toHaveLength(1);
  });

  it("renders nothing without a text", async () => {
    await render(undefined);

    // Svelte leaves the anchor of the empty branch in the DOM
    expect(target.children).toHaveLength(0);
  });

  it("applies the class to the block, so the rows inherit it", async () => {
    await render(["The mode", "of the task"], "text-muted-foreground flex-1");

    const block = target.firstElementChild!;
    expect(block.classList.contains("text-muted-foreground")).toBe(true);
    expect(block.classList.contains("flex-1")).toBe(true);
  });
});

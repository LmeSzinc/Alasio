/**
 * Tests of Arg.svelte, the dt -> component registry of the settings rows.
 *
 * dt="input-line" is the single line text box: it is drawn like the textarea of
 * dt="textarea" but edited by an <input>, so a newline can not be typed and
 * never breaks the line. The backend gives it the vertical layout, where the
 * input takes the whole width of the card.
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import Arg from "./Arg.svelte";
import type { ArgData } from "./utils.svelte";

let mounted: any[] = [];

function makeArg(overrides: Partial<ArgData> = {}): ArgData {
  return {
    task: "Test",
    group: "EmulatorInfo",
    arg: "Name",
    dt: "input-line",
    value: "some text",
    name: "Name",
    // the backend sets `layout: vert` for the textarea-like datatypes
    layout: "vert",
    ...overrides,
  };
}

async function mountArg(arg: ArgData) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const data = $state(arg);
  const handleEdit = vi.fn();
  const handleReset = vi.fn();
  mounted.push(mount(Arg, { target, props: { data, handleEdit, handleReset } }));
  await flushEffects();
  return { arg: data, target, handleEdit, handleReset };
}

function textInput(target: HTMLElement): HTMLInputElement {
  const input = target.querySelector("input");
  if (input === null) throw new Error("the row has no input");
  return input;
}

/** The bottom accent line dt="input" draws below the field */
function bottomLine(target: HTMLElement): Element | null {
  return target.querySelector(".border-b-2");
}

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
});

describe("TestArgInputLine", () => {
  it("edits the value with an input, not a textarea", async () => {
    const { target } = await mountArg(makeArg());

    expect(target.querySelector("textarea")).toBeNull();
    expect(textInput(target).type).toBe("text");
    expect(textInput(target).value).toBe("some text");
  });

  it("draws the textarea box instead of the bottom accent line", async () => {
    const { target } = await mountArg(makeArg());
    const input = textInput(target);

    // exact tokens: the base class of the shadcn input carries "file:border-0"
    expect(input.classList.contains("bg-accent")).toBe(true);
    expect(input.classList.contains("font-mono")).toBe(true);
    expect(input.classList.contains("border-0")).toBe(false);
    expect(bottomLine(target)).toBeNull();
  });

  it("keeps the flat row of dt=input unchanged", async () => {
    const { target } = await mountArg(makeArg({ dt: "input" }));
    const input = textInput(target);

    expect(input.classList.contains("border-0")).toBe(true);
    expect(input.classList.contains("bg-accent")).toBe(false);
    expect(bottomLine(target)).not.toBeNull();
  });

  it("drops the box in the desc layout, which shows plain text", async () => {
    const { target } = await mountArg(makeArg({ layout: "desc" }));
    const input = textInput(target);

    expect(input.classList.contains("bg-accent")).toBe(false);
    expect(input.classList.contains("border-0")).toBe(true);
  });

  it("shows a value with a newline on one line", async () => {
    const { target } = await mountArg(makeArg({ value: "first\nsecond" }));

    // <input type="text"> strips the newlines of its value, so the line never breaks
    expect(textInput(target).value).toBe("firstsecond");
  });

  it("does not rewrite a value with a newline when it is not edited", async () => {
    const { target, handleEdit } = await mountArg(makeArg({ value: "first\nsecond" }));
    const input = textInput(target);

    input.dispatchEvent(new Event("focus"));
    input.dispatchEvent(new Event("blur"));
    await flushEffects();

    expect(handleEdit).not.toHaveBeenCalled();
  });

  it("commits an edit on blur", async () => {
    const { arg, target, handleEdit } = await mountArg(makeArg());
    const input = textInput(target);

    input.value = "edited";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("blur"));
    await flushEffects();

    expect(arg.value).toBe("edited");
    expect(handleEdit).toHaveBeenCalledTimes(1);
  });
});

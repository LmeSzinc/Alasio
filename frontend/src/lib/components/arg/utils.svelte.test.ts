/**
 * Tests for useVisible(), the folded help of an arg row.
 *
 * A row's help folds behind its own question mark, and also opens while the
 * advanced settings of the card are open. The two inputs are getters, so the
 * composable follows them without being rebuilt; the question mark keeps its
 * own say on top of the expand.
 *
 * The file name ends with `.svelte.test.ts`, so the svelte plugin compiles it as
 * a svelte module: `$state` / `$effect.root` are available here.
 */
import { describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { useVisible } from "./utils.svelte";

/**
 * Create the composable inside an effect root, the way a component would, and
 * let it settle: the first run of its sync effect happens asynchronously, so a
 * test that acted before this resolves would act before the row is on screen.
 */
async function setup(foldHelp = true, expanded = false) {
  const state = $state({ foldHelp, expanded });
  let help!: ReturnType<typeof useVisible>;
  const destroy = $effect.root(() => {
    help = useVisible(
      () => state.foldHelp,
      () => state.expanded,
    );
  });
  await flushEffects();
  return { help: () => help, state, destroy };
}

describe("TestUseVisibleWhenFolded", () => {
  it("keeps a folded help hidden at first", async () => {
    const { help, destroy } = await setup();

    expect(help().shown).toBe(false);
    expect(help().visible).toBe(false);
    destroy();
  });

  it("shows it once the setting around it opens", async () => {
    const { help, state, destroy } = await setup();

    state.expanded = true;
    await flushEffects();

    expect(help().shown).toBe(true);
    destroy();
  });

  it("folds it back when the setting around it closes", async () => {
    const { help, state, destroy } = await setup();
    state.expanded = true;
    await flushEffects();

    state.expanded = false;
    await flushEffects();

    expect(help().shown).toBe(false);
    destroy();
  });

  it("expands it again on the next open, after a manual fold", async () => {
    const { help, state, destroy } = await setup();
    state.expanded = true;
    await flushEffects();
    // the question mark of the row folds the help while the setting stays open
    help().visible = false;
    await flushEffects();
    expect(help().shown).toBe(false);

    state.expanded = false;
    await flushEffects();
    state.expanded = true;
    await flushEffects();

    // opening the setting is one gesture for the whole row: it expands again
    expect(help().shown).toBe(true);
    destroy();
  });
});

describe("TestUseVisibleWhenToggledByHand", () => {
  it("keeps a hand-made fold while the setting around it stays open", async () => {
    const { help, state, destroy } = await setup();
    state.expanded = true;
    await flushEffects();

    help().visible = false;
    await flushEffects();
    // a second flush proves the sync did not rerun and pull it open again
    await flushEffects();

    expect(help().shown).toBe(false);
    expect(help().visible).toBe(false);
    destroy();
  });

  it("opens by hand without the setting around it", async () => {
    const { help, destroy } = await setup();

    help().visible = true;
    await flushEffects();

    expect(help().shown).toBe(true);
    help().visible = false;
    await flushEffects();
    expect(help().shown).toBe(false);
    destroy();
  });
});

describe("TestUseVisibleWhenNotFolded", () => {
  it("always shows a help that has no fold of its own", async () => {
    const { help, state, destroy } = await setup(false);

    expect(help().shown).toBe(true);
    state.expanded = true;
    await flushEffects();
    expect(help().shown).toBe(true);
    destroy();
  });
});

describe("TestUseVisibleWhenMountedExpanded", () => {
  it("starts expanded for a row that mounts behind an open setting", async () => {
    const { help, destroy } = await setup(true, true);

    // no frame of a folded help: the seed already has the state it mounts in
    expect(help().shown).toBe(true);
    destroy();
  });
});

<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import GoldenTestReview from "$private/dev/golden-test/GoldenTestReview.svelte";
  import { DEMO_FILES, DEMO_MOD, DEMO_SELECTED, DEMO_TREE } from "./mock";
  import screenshot from "./screenshot.png?url";

  /**
   * The golden test review page over fixed mock data.
   *
   * The review topics of the backend only serve the desktop client, so the real
   * page can not be opened while working on the layout without running the
   * whole app. This page renders the very same workspace (`GoldenTestReview`)
   * over one mock mod: the tree, the sources and their `@image` bindings are
   * constants of `mock.ts`, and every screenshot is the one bundled image.
   *
   * The mod selector of the app header is dropped: the mod is fixed here and the
   * `ModList` topic is not subscribed. Refresh re-mounts the page, which is the
   * mocked equivalent of the backend reparsing the selected file.
   */
  let selected = $state(DEMO_SELECTED);
  let showImages = $state(true);
  let reload = $state(0);

  const data = $derived(DEMO_FILES[selected] ?? {});
</script>

{#key reload}
  <GoldenTestReview
    dirs={DEMO_TREE}
    modName={DEMO_MOD}
    {selected}
    {data}
    bind:showImages
    initialExpanded={["map"]}
    resolveImage={() => screenshot}
    onSelect={(path) => (selected = path)}
    onSubFolder={() => {}}
    onUnsubFolder={() => {}}
    onRefresh={() => (reload += 1)}
  />
{/key}

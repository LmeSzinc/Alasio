<script lang="ts">
  import { PAGE_LAYER } from "$lib/context/stack.svelte";
  import DevCompactHeader from "$lib/dev/DevCompactHeader.svelte";
  import { t } from "$lib/i18n";
  import { HeaderContext } from "$lib/slotcontext.svelte";
  import { isElectron } from "$lib/use/useElectronEnv.svelte";
  import { DragRegionContext } from "$lib/window/dragRegion.svelte";
  import { frame } from "$lib/window/frame.svelte";
  import { modSelectorRegion } from "$lib/window/regions";
  import { useTopic, websocketClient } from "$lib/ws";
  import GoldenTestReview from "./GoldenTestReview.svelte";
  import type { GoldenTestFileData, GoldenTestTreeData, ModOption } from "./types";

  /**
   * The golden test review page: the tree of the `tests/` folder of a mod on the
   * left, the source of the selected test file with its screenshots in the
   * middle. The workspace itself is `GoldenTestReview`, which the demo page of
   * the dev tools renders over mock data.
   *
   * Both views live in one source per topic in the backend process, so this page
   * only drives them: `set_mod` browses a mod, `sub_folder` / `unsub_folder`
   * follow the open folders of the tree, `set_file` selects (or reloads) a file.
   * The backend pushes the changes of the files it watches, so an edit made in
   * an editor arrives here without asking for it.
   */

  // This page owns the header (title + mod selector) and the band that stops
  // left of the selector, like every compact dev page.
  HeaderContext.use(header, { priority: PAGE_LAYER });
  DragRegionContext.use((inherited) => modSelectorRegion(frame.controls.width)(inherited), { priority: PAGE_LAYER });

  const modTopic = useTopic<ModOption[]>("ModList");
  const treeTopic = useTopic<GoldenTestTreeData>("GoldenTestTree");
  const fileTopic = useTopic<GoldenTestFileData>("GoldenTestFile");

  const treeRpc = treeTopic.rpc();
  const fileRpc = fileTopic.rpc();

  const mods = $derived(Array.isArray(modTopic.data) ? modTopic.data : []);
  /** Mod of the backend view, the source of truth of what is browsed */
  const modName = $derived(treeTopic.data?.mod_name ?? "");
  const dirs = $derived(treeTopic.data?.dirs ?? {});
  const fileData: GoldenTestFileData = $derived(fileTopic.data ?? {});
  const selected = $derived(fileData.file ?? "");

  /** Mod of the dropdown: it follows the view, the view follows its edits */
  let chosen = $state("");
  /** Connection the auto selection of a mod already ran on */
  let autoselected = -1;
  let showImages = $state(true);
  /**
   * False until the reset of the mount finished: the file panel stays empty
   * meanwhile, so a file another window selected never flashes on open
   */
  let ready = $state(false);

  // The view of the process is shared: a second window, or a reload of this
  // one, may browse a mod this page did not pick
  $effect(() => {
    const current = treeTopic.data?.mod_name ?? "";
    if (current !== "") chosen = current;
  });

  // A page that opens resets the view it shows: the backend view is shared by
  // every connection, and another window (or a page left open earlier) may
  // have a file selected. Re-selecting the mod clears the selected file and the
  // expanded folders, so the page always starts on the tree alone, once per
  // connection generation (a reconnect resets as well, a re-render does not).
  $effect(() => {
    const generation = websocketClient.connectionGeneration;
    const data = treeTopic.data;
    if (data === undefined) return;
    if (autoselected === generation) return;
    const current = data.mod_name ?? "";
    const target = current !== "" ? current : chosen !== "" ? chosen : mods[0]?.value;
    if (target === undefined || target === "") return;
    autoselected = generation;
    chosen = target;
    // A failure (a timeout while the backend scans, a stale connection) must not
    // leave the page with no file panel at all: it opens like a manual pick
    treeRpc.call("set_mod", { mod_name: target }, { onSuccess: () => (ready = true), onError: () => (ready = true) });
  });

  function handleModChange(value: string) {
    if (value === "" || value === modName) return;
    chosen = value;
    ready = true;
    treeRpc.call("set_mod", { mod_name: value });
  }

  function handleSelect(path: string) {
    if (modName === "" || path === selected) return;
    ready = true;
    fileRpc.call("set_file", { mod_name: modName, file: path });
  }

  function handleSubFolder(path: string) {
    treeRpc.call("sub_folder", { path });
  }

  function handleUnsubFolder(path: string) {
    treeRpc.call("unsub_folder", { path });
  }

  function handleRefresh() {
    // Selecting the selected file again reparses it: the page has no refresh RPC
    if (modName === "" || selected === "") return;
    fileRpc.call("set_file", { mod_name: modName, file: selected });
  }

  const desktopOnly = $derived(!isElectron.value);
</script>

{#snippet header()}
  <DevCompactHeader title={t.GoldenTest.Title()} mod_name={chosen} handleEdit={handleModChange} />
{/snippet}

<GoldenTestReview
  {modName}
  {dirs}
  {selected}
  data={ready ? fileData : {}}
  bind:showImages
  empty={modName === "" ? (desktopOnly ? t.GoldenTest.DesktopOnly() : t.GoldenTest.NoMod()) : ""}
  onSelect={handleSelect}
  onSubFolder={handleSubFolder}
  onUnsubFolder={handleUnsubFolder}
  onRefresh={handleRefresh}
/>

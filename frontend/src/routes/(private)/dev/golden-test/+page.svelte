<script lang="ts">
  import Images from "@lucide/svelte/icons/images";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Toggle } from "$lib/components/ui/toggle/index.js";
  import { t } from "$lib/i18n";
  import { isElectron } from "$lib/use/useElectronEnv.svelte";
  import { useTopic, websocketClient } from "$lib/ws";
  import SourceView from "./SourceView.svelte";
  import TestTree from "./TestTree.svelte";
  import type { GoldenTestFileData, GoldenTestTreeData, ModOption } from "./types";

  /**
   * The golden test review page: the tree of the `tests/` folder of a mod on the
   * left, the source of the selected test file with its screenshots in the
   * middle.
   *
   * Both views live in one source per topic in the backend process, so this page
   * only drives them: `set_mod` browses a mod, `sub_folder` / `unsub_folder`
   * follow the open folders of the tree, `set_file` selects (or reloads) a file.
   * The backend pushes the changes of the files it watches, so an edit made in
   * an editor arrives here without asking for it.
   */

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

<div class="bg-background flex h-full min-h-0 w-full flex-col">
  <!-- The toolbar lives in the page, not in the app header: the dev nav owns the
       header slot of this layout and would replace a snippet of the page. Its
       first cell is as wide as the tree column, so the file path starts on the
       same line as the code below it. -->
  <div class="border-border flex shrink-0 items-center border-b pr-3">
    <div class="border-border w-64 shrink-0 border-r p-2">
      <ModSelector class="w-full" mod_name={chosen} handleEdit={handleModChange} />
    </div>
    <span class="text-muted-foreground min-w-0 flex-1 truncate px-2 font-mono text-xs" title={selected}>
      {selected}
    </span>
    <div class="flex shrink-0 items-center gap-2">
      <Toggle
        bind:pressed={showImages}
        variant="outline"
        size="sm"
        aria-label={t.GoldenTest.ShowImages()}
        title={t.GoldenTest.ShowImages()}
      >
        <Images class="size-3.5" />
        <span class="text-xs">{t.GoldenTest.ShowImages()}</span>
      </Toggle>
      <Button variant="outline" size="sm" disabled={selected === ""} onclick={handleRefresh}>
        <RefreshCw class="size-3.5" />
        <span class="text-xs">{t.GoldenTest.Refresh()}</span>
      </Button>
    </div>
  </div>

  <div class="flex min-h-0 flex-1">
    <div class="border-border w-64 shrink-0 overflow-auto border-r">
      {#if modName === ""}
        <div class="text-muted-foreground p-3 text-xs">
          {desktopOnly ? t.GoldenTest.DesktopOnly() : t.GoldenTest.NoMod()}
        </div>
      {:else}
        <!-- A new mod starts with no folder open -->
        {#key modName}
          <TestTree
            {dirs}
            {selected}
            onSelect={handleSelect}
            onSubFolder={handleSubFolder}
            onUnsubFolder={handleUnsubFolder}
          />
        {/key}
      {/if}
    </div>

    <div class="flex min-w-0 flex-1 flex-col">
      <SourceView data={ready ? fileData : {}} {showImages} />
    </div>
  </div>
</div>

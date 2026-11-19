<script lang="ts">
  import { PAGE_LAYER } from "$lib/context/stack.svelte";
  import DevCompactHeader from "$lib/dev/DevCompactHeader.svelte";
  import { t } from "$lib/i18n";
  import { HeaderContext } from "$lib/slotcontext.svelte";
  import { DragRegionContext } from "$lib/window/dragRegion.svelte";
  import { frame } from "$lib/window/frame.svelte";
  import { modSelectorRegion } from "$lib/window/regions";
  import { useTopic } from "$lib/ws";
  import AssetManager from "./AssetManager.svelte";
  import ImageViewer from "./ImageViewer.svelte";
  import PathBreadcrumb from "./PathBreadcrumb.svelte";
  import ResourceManager from "./ResourceManager.svelte";
  import StatusBar from "./StatusBar.svelte";
  import { templateSelection } from "./selected.svelte";
  import type { FolderResponse } from "./types";

  // header context: this page owns the whole header bar (title + selector), so
  // its snippet outranks the one the dev layout declares.
  HeaderContext.use(header, { priority: PAGE_LAYER });
  // The selector sits in the header and is the only interactive control there,
  // so the drag band stops left of it. The left side stays what the layout
  // declared, which keeps the aside config column clickable.
  DragRegionContext.use((inherited) => modSelectorRegion(frame.controls.width)(inherited), { priority: PAGE_LAYER });

  const topicClient = useTopic<FolderResponse>("DevAssetsManager");
  const modRpc = topicClient.rpc();
  const pathRpc = topicClient.rpc();

  const mod_name = $derived(topicClient.data?.mod_name || "");
  const path = $derived(topicClient.data?.path || "");
  const mod_path_assets = $derived(topicClient.data?.mod_path_assets || "assets");

  /** Template the viewer shows: the single selected one, if there is exactly one */
  const viewedTemplate = $derived(
    templateSelection.count === 1 ? (templateSelection.selectedItems[0]?.file ?? "") : "",
  );
  const viewedUrl = $derived(
    viewedTemplate !== "" && mod_name !== "" ? `/api/dev_assets/${mod_name}/${viewedTemplate}` : null,
  );

  function handleModChange(value: string) {
    modRpc.call("set_mod", { mod_name: value });
  }
  function handleNavigate(path: string) {
    pathRpc.call("set_path", { path: path });
  }
</script>

{#snippet header()}
  <DevCompactHeader title={t.AssetManager.AssetManager()} {mod_name} handleEdit={handleModChange} />
{/snippet}

<!--
  A 2×2 workspace, not a page with a sidebar: the four quadrants are the tree
  of the folder, the template viewer, the assets and templates of the folder,
  and the editor. It runs without the navigation column, so the aside holds
  only the config column.

  The width floor keeps every quadrant usable (384px each, which fits three
  108px resource tiles); below it the page scrolls sideways instead of folding
  into a different layout. A narrow window therefore shows a slice of the same
  workspace, which is all a phone needs from a desktop developer tool.
-->
<div class="bg-background flex h-full min-h-0 w-full min-w-[768px] flex-col">
  <div class="grid min-h-0 flex-1 grid-cols-2 grid-rows-2">
    <!-- Top left: the folder tree and the files it holds -->
    <div class="border-border flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-b">
      <PathBreadcrumb class="bg-card shrink-0" {mod_path_assets} {path} onNavigate={handleNavigate} />
      <ResourceManager class="min-h-0 flex-1 overflow-hidden" {mod_name} {path} {topicClient} />
      <StatusBar data={topicClient.data} class="bg-card shrink-0" />
    </div>

    <!-- Top right: the selected template -->
    <div class="border-border flex min-h-0 min-w-0 flex-col overflow-hidden border-b">
      <ImageViewer src={viewedUrl} class="min-h-0 flex-1" />
    </div>

    <!-- Bottom left: assets of the folder, and the templates of the asset -->
    <div class="border-border flex min-h-0 min-w-0 flex-col overflow-hidden border-r">
      <AssetManager class="min-h-0 flex-1 border-t-0" {mod_name} {path} {topicClient} />
    </div>

    <!-- Bottom right: the asset editor -->
    <div class="flex min-h-0 min-w-0 flex-col overflow-hidden p-3 text-sm">Editor</div>
  </div>
</div>

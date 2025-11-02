<script lang="ts">
  import { ScrollArea, Scrollbar } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils";
  import { CloudOff, Folder, Image, Layers, Link, Target, Unlink } from "@lucide/svelte";
  import type { FolderResponse } from "./types";

  interface Props {
    data?: FolderResponse;
    class?: string;
  }

  let { data, class: className = "" }: Props = $props();

  const resourcesArray = $derived(Object.values(data?.resources || {}));
  const assetsArray = $derived(Object.values(data?.assets || {}));

  const folders = $derived(data?.folders?.length || 0);
  const assets = $derived(assetsArray.length);
  const templates = $derived(assetsArray.reduce((sum, asset) => sum + (asset.templates?.length || 0), 0));

  const totalResources = $derived(resourcesArray.length);
  const tracked = $derived(resourcesArray.filter((r) => r.status === "tracked").length);
  const not_tracked = $derived(resourcesArray.filter((r) => r.status === "not_tracked").length);
  const not_downloaded = $derived(resourcesArray.filter((r) => r.status === "not_downloaded").length);
</script>

<ScrollArea class={cn("w-full", className)}>
  <div class="flex items-center gap-2 px-2 py-1 text-xs">
    <div class="flex flex-shrink-0 items-center gap-1 whitespace-nowrap">
      <Folder class="text-muted-foreground h-3 w-3" />
      <span class="text-muted-foreground">Folders:</span>
      <span class="text-foreground font-medium">{folders}</span>
    </div>

    <div class="flex flex-shrink-0 items-center gap-1 whitespace-nowrap">
      <Image class="text-muted-foreground h-3 w-3" />
      <span class="text-muted-foreground">Resources:</span>
      <span class="text-foreground font-medium">{totalResources}</span>
      <span class="text-muted-foreground">(</span>
      <Link class="h-3 w-3 text-green-500" />
      <span class="text-muted-foreground">Tracked:</span>
      <span class="text-foreground font-medium">{tracked}</span>
      {#if not_tracked > 0}
        <span class="text-muted-foreground">,</span>
        <Unlink class="h-3 w-3 text-orange-500" />
        <span class="text-muted-foreground">NotTracked:</span>
        <span class="text-foreground font-medium">{not_tracked}</span>
      {/if}
      {#if not_downloaded > 0}
        <span class="text-muted-foreground">,</span>
        <CloudOff class="h-3 w-3 text-yellow-500" />
        <span class="text-muted-foreground">NotDownloaded:</span>
        <span class="text-foreground font-medium">{not_downloaded}</span>
      {/if}
      <span class="text-muted-foreground">)</span>
    </div>

    <div class="flex flex-shrink-0 items-center gap-1 whitespace-nowrap">
      <Target class="text-muted-foreground h-3 w-3" />
      <span class="text-muted-foreground">Assets:</span>
      <span class="text-foreground font-medium">{assets}</span>
    </div>

    <div class="flex flex-shrink-0 items-center gap-1 whitespace-nowrap">
      <Layers class="text-muted-foreground h-3 w-3" />
      <span class="text-muted-foreground">Templates:</span>
      <span class="text-foreground font-medium">{templates}</span>
    </div>
  </div>
  <Scrollbar orientation="horizontal" class="h-2" />
</ScrollArea>

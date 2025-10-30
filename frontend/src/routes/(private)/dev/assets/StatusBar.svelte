<script lang="ts">
  import { cn } from "$lib/utils";
  import { Folder, Target, Image, CloudOff, Unlink, Link, Layers } from "@lucide/svelte";
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

<div class={cn("bg-muted/50 border-border flex items-center gap-4 border-y px-4 py-2 text-sm", className)}>
  <!-- Folders -->
  <div class="flex items-center gap-1.5">
    <Folder class="text-muted-foreground h-4 w-4" />
    <span class="text-muted-foreground">Folders:</span>
    <span class="text-foreground font-medium">{folders}</span>
  </div>

  <!-- Resources with status breakdown -->
  <div class="flex items-center gap-1.5">
    <Image class="text-muted-foreground h-4 w-4" />
    <span class="text-muted-foreground">Resources:</span>
    <span class="text-foreground font-medium">{totalResources}</span>
    <span class="text-muted-foreground">(</span>
    <Link class="h-3.5 w-3.5 text-green-500" />
    <span class="text-muted-foreground">Tracked:</span>
    <span class="text-foreground font-medium">{tracked}</span>
    {#if not_tracked > 0}
      <span class="text-muted-foreground">,</span>
      <Unlink class="h-3.5 w-3.5 text-orange-500" />
      <span class="text-muted-foreground">NotTracked:</span>
      <span class="text-foreground font-medium">{not_tracked}</span>
    {/if}
    {#if not_downloaded > 0}
      <span class="text-muted-foreground">,</span>
      <CloudOff class="h-3.5 w-3.5 text-yellow-500" />
      <span class="text-muted-foreground">NotDownloaded:</span>
      <span class="text-foreground font-medium">{not_downloaded}</span>
    {/if}
    <span class="text-muted-foreground">)</span>
  </div>

  <!-- Assets -->
  <div class="flex items-center gap-1.5">
    <Target class="text-muted-foreground h-4 w-4" />
    <span class="text-muted-foreground">Assets:</span>
    <span class="text-foreground font-medium">{assets}</span>
  </div>

  <!-- Templates -->
  <div class="flex items-center gap-1.5">
    <Layers class="text-muted-foreground h-4 w-4" />
    <span class="text-muted-foreground">Templates:</span>
    <span class="text-foreground font-medium">{templates}</span>
  </div>
</div>

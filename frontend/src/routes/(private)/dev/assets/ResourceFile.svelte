<script lang="ts">
  import ResourceDisplay from "./ResourceDisplay.svelte";
  import { Image } from "$lib/components/ui/image";
  import { cn } from "$lib/utils.js";
  import { CloudOff } from "@lucide/svelte";
  import type { ResourceItem } from "./types";

  let {
    resource,
    mod_name,
    currentPath = "",
  }: {
    resource: ResourceItem;
    mod_name: string;
    currentPath?: string;
  } = $props();

  /**
   * Construct the image URL from the current path and filename.
   */
  const imageUrl = $derived.by(() => {
    if (!resource.exist) return null;
    const pathParts = currentPath ? [currentPath, resource.name] : [resource.name];
    return `/api/dev_assets/${mod_name}/${pathParts.join("/")}`;
  });

  /**
   * Determine the appropriate status badge based on file state.
   */
  const statusBadge = $derived.by(() => {
    if (!resource.exist && resource.track) {
      return { text: "Not Downloaded", color: "bg-yellow-500" };
    }
    if (resource.exist && !resource.track) {
      return { text: "Untracked", color: "bg-orange-500" };
    }
    if (resource.exist && resource.track) {
      return { text: "Tracked", color: "bg-green-500" };
    }
    return null;
  });
</script>

<ResourceDisplay name={resource.displayName}>
  {#snippet content()}
    {#if resource.exist && imageUrl}
      <Image src={imageUrl} alt={resource.displayName} />
    {:else}
      <div class="bg-muted absolute inset-0 flex flex-col items-center justify-center">
        <CloudOff class="text-muted-foreground h-1/3 w-1/3" />
      </div>
    {/if}
  {/snippet}

  {#snippet badge()}
    {#if statusBadge}
      <div class={cn("rounded px-2 py-1 text-xs font-medium text-white shadow-sm", statusBadge.color)}>
        {statusBadge.text}
      </div>
    {/if}
  {/snippet}
</ResourceDisplay>

<script lang="ts">
  import { Image } from "$lib/components/ui/image";
  import { cn } from "$lib/utils.js";
  import { CloudOff } from "@lucide/svelte";
  import type { ResourceItem } from "./types";

  /**
   * Accept a ResourceItem object containing all resource metadata.
   * This approach is more maintainable than individual props because:
   * 1. Adding new resource properties only requires updating the type, not all call sites
   * 2. It's clearer that these properties are related and describe a single resource
   * 3. It reduces the number of props, making the component API simpler
   */
  let {
    resource,
    currentPath = "",
  }: {
    resource: ResourceItem;
    currentPath?: string;
  } = $props();

  /**
   * Construct the image URL from the current path and filename.
   */
  const imageUrl = $derived.by(() => {
    if (!resource.exist) return null;
    const pathParts = currentPath ? [currentPath, resource.name] : [resource.name];
    return `/assets/${pathParts.join("/")}`;
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

<div
  class={cn(
    "group relative aspect-square w-full rounded-lg border-2",
    "bg-card cursor-pointer overflow-hidden transition-all duration-200 hover:shadow-md",
    resource.exist ? "border-border hover:border-primary" : "border-border border-dashed",
  )}
>
  <div class="relative h-full w-full">
    {#if resource.exist && imageUrl}
      <Image src={imageUrl} alt={resource.displayName} />
    {:else}
      <div class="bg-muted absolute inset-0 flex flex-col items-center justify-center">
        <CloudOff class="text-muted-foreground h-1/3 w-1/3" />
      </div>
    {/if}

    <div
      class={cn(
        "absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent",
        "opacity-0 transition-opacity duration-200 group-hover:opacity-100",
      )}
    ></div>
  </div>

  {#if statusBadge}
    <div
      class={cn("absolute top-2 right-2 rounded px-2 py-1 text-xs font-medium text-white shadow-sm", statusBadge.color)}
    >
      {statusBadge.text}
    </div>
  {/if}

  <div class="absolute right-0 bottom-0 left-0 p-2">
    <p
      class={cn(
        "text-card-foreground text-xs font-medium group-hover:text-white",
        "bg-card/90 rounded px-2 py-1 group-hover:bg-transparent",
        "line-clamp-2 break-all transition-all",
      )}
    >
      {resource.displayName}
    </p>
  </div>
</div>

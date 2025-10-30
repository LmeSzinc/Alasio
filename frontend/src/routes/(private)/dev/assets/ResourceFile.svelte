<script lang="ts">
  import { Image } from "$lib/components/ui/image";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { CircleHelp, CloudOff, Unlink } from "@lucide/svelte";
  import ResourceDisplay from "./ResourceDisplay.svelte";
  import type { ResourceItem } from "./types";

  let {
    resource,
    mod_name,
    currentPath = "",
    selected,
    handleSelect,
    handleOpen,
    class: className,
  }: {
    resource: ResourceItem;
    mod_name: string;
    currentPath?: string;
    selected?: boolean;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    class?: string;
  } = $props();

  /**
   * Construct the image URL from the current path and filename.
   */
  const imageUrl = $derived.by(() => {
    // Show image if file exists (tracked or not_tracked status)
    if (resource.status === "not_downloaded") return null;
    const pathParts = currentPath ? [currentPath, resource.name] : [resource.name];
    return `/api/dev_assets/${mod_name}/${pathParts.join("/")}`;
  });
</script>

<ResourceDisplay name={resource.displayName} {selected} {handleSelect} {handleOpen} class={className}>
  {#snippet content()}
    {#if resource.status !== "not_downloaded" && imageUrl}
      <Image src={imageUrl} alt={resource.displayName} />
    {:else}
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <CloudOff class="text-muted-foreground h-1/3 w-1/3" />
      </div>
    {/if}
  {/snippet}

  {#snippet badge()}
    {#if resource.status === "not_downloaded"}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="cursor-help rounded bg-yellow-500/90 p-1 text-white shadow-sm">
              <CloudOff class="h-4 w-4" />
            </div>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>Not Downloaed</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {:else if resource.status === "not_tracked"}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="cursor-help rounded bg-orange-500/90 p-1 text-white shadow-sm">
              <Unlink class="h-4 w-4" />
            </div>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>No Tracked</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {:else if resource.status === "tracked"}
      <!-- No badge for tracked files -->
    {:else}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="cursor-help rounded bg-gray-500/90 p-1 text-white shadow-sm">
              <CircleHelp class="h-4 w-4" />
            </div>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>Unknown</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {/if}
  {/snippet}
</ResourceDisplay>

<script lang="ts">
  import { Image } from "$lib/components/ui/image";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { CircleHelp, CloudOff, Link, Unlink } from "@lucide/svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import ResourceDisplay from "./ResourceDisplay.svelte";
  import type { ResourceItem } from "./types";
  import type { ResourceSelectionItem } from "./selected.svelte";

  let {
    mod_name,
    resourceItem,
    item,
    currentPath = "",
    handleSelect,
    handleOpen,
    handleRename,
    class: className,
    ...restProps
  }: {
    mod_name: string;
    resourceItem: ResourceItem;
    item: ResourceSelectionItem;
    currentPath?: string;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    handleRename?: (oldName: string, newName: string) => void;
    class?: string;
  } & HTMLAttributes<HTMLDivElement> = $props();

  /**
   * Construct the image URL from the current path and filename.
   */
  const imageUrl = $derived.by(() => {
    // Show image if file exists (tracked or not_tracked status)
    if (resourceItem.status === "not_downloaded") return null;
    const pathParts = currentPath ? [currentPath, resourceItem.name] : [resourceItem.name];
    return `/api/dev_assets/${mod_name}/${pathParts.join("/")}`;
  });
</script>

<ResourceDisplay
  name={resourceItem.displayName}
  {item}
  {handleSelect}
  {handleOpen}
  {handleRename}
  class={className}
  {...restProps}
>
  {#snippet content()}
    {#if resourceItem.status !== "not_downloaded" && imageUrl}
      <Image src={imageUrl} alt={resourceItem.displayName} rootMargin="0px" />
    {:else}
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <CloudOff class="text-muted-foreground h-1/3 w-1/3" />
      </div>
    {/if}
  {/snippet}

  {#snippet badge()}
    {#if resourceItem.status === "not_downloaded"}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="bg-card cursor-help rounded p-1 shadow-sm">
              <CloudOff class="h-3 w-3 text-yellow-500" />
            </div>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>Not Downloaed</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {:else if resourceItem.status === "not_tracked"}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="bg-card cursor-help rounded p-1 shadow-sm">
              <Unlink class="h-3 w-3 text-orange-500" />
            </div>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>No Tracked</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {:else if resourceItem.status === "tracked"}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="bg-card cursor-help rounded p-1 shadow-sm">
              <Link class="h-3 w-3 text-green-500" />
            </div>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>Tracked</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {:else}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <div class="bg-card cursor-help rounded p-1 shadow-sm">
              <CircleHelp class="h-3 w-3 text-gray-500" />
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

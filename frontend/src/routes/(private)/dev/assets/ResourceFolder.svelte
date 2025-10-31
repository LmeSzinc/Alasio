<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { Folder } from "@lucide/svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import ResourceDisplay from "./ResourceDisplay.svelte";
  import type { ResourceSelectionItem } from "./selected.svelte";

  let {
    name,
    item,
    handleSelect,
    handleOpen,
    handleRename,
    class: className,
    ...restProps
  }: {
    name: string;
    item: ResourceSelectionItem;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    handleRename?: (oldName: string, newName: string) => void;
    class?: string;
  } & HTMLAttributes<HTMLDivElement> = $props();
</script>

<ResourceDisplay {name} {item} {handleSelect} {handleOpen} {handleRename} class={className} {...restProps}>
  {#snippet content()}
    <div class="absolute inset-0 flex flex-col items-center justify-center">
      <Folder
        class={cn("text-muted-foreground group-hover:text-primary h-2/3 w-2/3 transition-colors")}
        strokeWidth={1.5}
      />
    </div>
  {/snippet}
</ResourceDisplay>

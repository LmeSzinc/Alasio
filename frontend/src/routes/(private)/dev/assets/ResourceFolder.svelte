<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { Folder } from "@lucide/svelte";
  import ResourceDisplay from "./ResourceDisplay.svelte";

  let {
    name,
    selected,
    handleSelect,
    handleOpen,
    handleRename,
    class: className,
  }: {
    name: string;
    selected?: boolean;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    handleRename?: (oldName: string, newName: string) => void;
    class?: string;
  } = $props();
</script>

<ResourceDisplay {name} itemType="folder" {selected} {handleSelect} {handleOpen} {handleRename} class={className}>
  {#snippet content()}
    <div class="absolute inset-0 flex flex-col items-center justify-center">
      <Folder
        class={cn("text-muted-foreground group-hover:text-primary h-2/3 w-2/3 transition-colors")}
        strokeWidth={1.5}
      />
    </div>
  {/snippet}
</ResourceDisplay>

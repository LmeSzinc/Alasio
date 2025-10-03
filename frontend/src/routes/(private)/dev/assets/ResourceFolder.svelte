<script lang="ts">
  import ResourceDisplay from "./ResourceDisplay.svelte";
  import { Folder } from "@lucide/svelte";
  import { cn } from "$lib/utils.js";

  let {
    name,
    onOpen,
  }: {
    name: string;
    onOpen?: () => void;
  } = $props();
</script>

<ResourceDisplay {name} ondblclick={onOpen}>
  {#snippet content()}
    <div class="absolute inset-0 flex flex-col items-center justify-center">
      <Folder
        class={cn("text-muted-foreground group-hover:text-primary h-2/3 w-2/3 transition-colors")}
        strokeWidth={1.5}
      />
    </div>
  {/snippet}
</ResourceDisplay>

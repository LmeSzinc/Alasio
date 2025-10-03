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
    <div class="absolute inset-0 flex flex-col items-center justify-center p-4">
      <div class="mb-2">
        <Folder
          class={cn("text-muted-foreground group-hover:text-primary h-16 w-16 transition-colors")}
          strokeWidth={1.5}
        />
      </div>

      <!-- Background highlight effect on hover -->
      <div
        class={cn(
          "bg-primary/5 absolute inset-0 rounded-lg opacity-0",
          "pointer-events-none transition-opacity group-hover:opacity-100",
        )}
      ></div>
    </div>
  {/snippet}
</ResourceDisplay>

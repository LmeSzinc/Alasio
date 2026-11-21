<script lang="ts">
  import Image from "$lib/components/ui/image/Image.svelte";
  import { cn } from "$lib/utils";
  import type { MetaTemplate } from "./types";

  let {
    template,
    mod_name,
    showFrameInfo = false,
    class: className,
  }: {
    template: MetaTemplate;
    mod_name: string;
    showFrameInfo?: boolean;
    class?: string;
  } = $props();
</script>

<div class={cn("group relative shrink-0", className)}>
  <Image src={`/api/dev_assets/${mod_name}/${template.file}`} alt={`Template from ${template.file}`} rootMargin="0px" />
  {#if showFrameInfo}
    <div
      class="absolute bottom-0 w-full rounded-b-sm bg-background/80 px-1 py-0.5 text-center text-xs text-foreground opacity-0 transition-opacity group-hover:opacity-100"
    >
      Frame: {template.frame}
    </div>
  {/if}
</div>

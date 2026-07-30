<script lang="ts">
  import { cn } from "$lib/utils";

  interface Props {
    text: string;
    class?: string;
  }
  let { text = "", class: className = "" }: Props = $props();

  let containerRef = $state<HTMLElement | null>(null);
  let probeLgRef = $state<HTMLElement | null>(null);
  let probeBaseRef = $state<HTMLElement | null>(null);
  type Mode = "lg" | "base" | "sm-clamp";
  let mode = $state<Mode>("base");

  $effect(() => {
    containerRef;
    probeLgRef;
    probeBaseRef;
    if (!containerRef || !probeLgRef || !probeBaseRef) return;
    const cw = containerRef.clientWidth;
    if (probeLgRef.scrollWidth <= cw) {
      mode = "lg";
    } else if (probeBaseRef.scrollWidth <= cw) {
      mode = "base";
    } else {
      mode = "sm-clamp";
    }
  });
</script>

<div bind:this={containerRef} class={cn("relative overflow-hidden", className)}>
  <!-- Probe text-lg -->
  <span
    bind:this={probeLgRef}
    class="invisible absolute top-0 left-0 whitespace-nowrap text-lg font-semibold"
    aria-hidden="true"
  >{text}</span>
  <!-- Probe text-base -->
  <span
    bind:this={probeBaseRef}
    class="invisible absolute top-0 left-0 whitespace-nowrap text-base font-semibold"
    aria-hidden="true"
  >{text}</span>
  <!-- Display -->
  <span
    class={cn(
      "font-semibold",
      mode === "lg" && "truncate text-lg",
      mode === "base" && "truncate text-base",
      mode === "sm-clamp" && "line-clamp-2 text-sm wrap-break-word",
    )}
  >{text}</span>
</div>

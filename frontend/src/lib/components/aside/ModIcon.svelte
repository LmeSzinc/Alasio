<script lang="ts">
  import Play from "@lucide/svelte/icons/play";
  import { cn } from "$lib/utils.js";

  type Props = {
    mod: string;
    // Easter egg spinning
    // afspin should be True on April 1st
    afspin?: boolean;
    class?: string;
  };
  let { mod, afspin = false, class: className }: Props = $props();

  // The mod whose icon failed to load. The failure is remembered per mod
  // instead of a boolean that an effect clears whenever it reads the prop: a
  // re-render that keeps the mod (the topic hands over a new config object
  // carrying the same mod name) cleared such a flag, which put the <img> back
  // on screen, had the browser request the same file again and dropped back to
  // the fallback once it failed -- the sidebar icon flickered although the
  // data had not changed. A different mod is a different file, and it is
  // tried.
  let failedMod = $state<string | null>(null);
  const failed = $derived(failedMod === mod);

  function handleIconError() {
    failedMod = mod;
  }
</script>

{#if mod && !failed}
  <img
    src="/static/icon/{mod}.svg"
    alt=""
    aria-hidden="true"
    class={cn("h-8 w-8 object-contain", afspin && "origin-[50%_42%] animate-[spin_400ms_linear_infinite]", className)}
    onerror={handleIconError}
  />
{:else}
  <Play
    class={cn("h-8 w-8", afspin && "origin-[50%_42%] animate-[spin_400ms_linear_infinite]", className)}
    strokeWidth="1.5"
    aria-hidden="true"
  />
{/if}

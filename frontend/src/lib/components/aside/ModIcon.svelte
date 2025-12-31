<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { Play } from "@lucide/svelte";

  type Props = {
    mod: string;
    // Easter egg spinning
    // afspin should be True on April 1st
    afspin?: boolean;
    class?: string;
  };
  let { mod, afspin = false, class: className }: Props = $props();

  let iconError = $state(false);

  $effect(() => {
    mod;
    iconError = false;
  });

  function handleIconError() {
    iconError = true;
  }
</script>

{#if mod && !iconError}
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

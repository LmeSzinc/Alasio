<script lang="ts">
  import { useSharedState } from "$lib/useSharedState.svelte";
  import * as t from "$lib/i18n/error";

  const sharedState = useSharedState();
</script>

<div class="flex h-screen items-center justify-center bg-background text-foreground">
  <div class="w-[600px] rounded-xl bg-card backdrop-blur-lg p-12 shadow-lg">
    <h1 class="text-4xl font-bold mb-6 text-destructive">Error</h1>

    <div class="mb-6">
      <p class="text-xl mb-4">
        {sharedState.errorMessage || "Unknown error"}
      </p>
      <div class="text-sm">
        <p>{t.CurrentPath()}: {sharedState.errorMessage}</p>
      </div>
    </div>

    <button
      onclick={() => location.reload()}
      class="w-full py-3 bg-primary hover:bg-primary/30 text-destructive-foreground font-semibold rounded-lg transition-colors"
    >
      Retry
    </button>
  </div>
</div>

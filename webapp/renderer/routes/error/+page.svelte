<script lang="ts">
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { t } from "$lib/i18n";

  const sharedState = useSharedState();
</script>

<div class="bg-background text-foreground flex h-screen items-center justify-center">
  <div class="bg-card w-[600px] rounded-xl p-12 shadow-lg backdrop-blur-lg">
    <h1 class="text-destructive mb-6 text-4xl font-bold">Error</h1>

    <div class="mb-6">
      <p class="mb-4 text-xl">
        {sharedState.errorMessage || "Unknown error"}
      </p>
      <div class="text-sm">
        <p>{t.Error.CurrentPath()}: {sharedState.errorMessage}</p>
      </div>
    </div>

    <button
      onclick={() => location.reload()}
      class="bg-primary hover:bg-primary/30 text-destructive-foreground w-full rounded-lg py-3 font-semibold transition-colors"
    >
      Retry
    </button>
  </div>
</div>

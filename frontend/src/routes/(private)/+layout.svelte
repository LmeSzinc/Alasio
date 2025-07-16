<script lang="ts">
  import { invalidateAll } from "$app/navigation";
  import ConfigAside from "$lib/components/aside/ConfigAside.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  let { data, children } = $props();

  let isRetrying = $state<boolean>(false);
  $effect(() => {
    data;
    isRetrying = false;
  });
  function handleRetry() {
    isRetrying = true;
    invalidateAll();
  }
</script>

{#if data.success}
  <div class="app-container">
    <!-- <Header /> -->
    <ConfigAside />
    <main class="app-content">
      {@render children()}
    </main>
  </div>
{:else}
  <div class="bg-background fixed inset-0 z-50 flex items-center justify-center">
    <Card.Root class="animate-in fade-in-0 zoom-in-95 mx-4 w-full max-w-md">
      <Card.Header class="text-center">
        <Card.Title class="text-2xl font-semibold">Oops!</Card.Title>
        <Card.Description>Authorization failed</Card.Description>
        <Card.Content class="mt-2">
          {#if isRetrying}
            (Retrying)
          {:else if data.errorMsg}
            {data.errorMsg}
          {:else}
            (No error message)
          {/if}
        </Card.Content>
      </Card.Header>
      <Card.Footer>
        <Button onclick={handleRetry} class="w-full">
          {#if isRetrying}
            Retrying
          {:else}
            Click to Retry
          {/if}
        </Button>
      </Card.Footer>
    </Card.Root>
  </div>
{/if}

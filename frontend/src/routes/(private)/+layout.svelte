<script lang="ts">
  import { invalidateAll } from "$app/navigation";
  import ConfigAside from "$lib/components/aside/ConfigAside.svelte";
  import { Header } from "$lib/components/header";
  import { MainNav } from "$lib/components/nav";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import * as Sheet from "$lib/components/ui/sheet";

  // props
  let { data, children } = $props();

  // error page
  let isRetrying = $state<boolean>(false);
  $effect(() => {
    data;
    isRetrying = false;
  });
  function handleRetry() {
    isRetrying = true;
    invalidateAll();
  }

  // sheet state
  let isSheetOpen = $state(false);
  const openSheet = () => (isSheetOpen = true);
  const closeSheet = () => (isSheetOpen = false);
</script>

{#if data.success}
  <!-- 1. Main layout -->
  <div class="app-container flex h-screen w-full flex-col">
    <!-- 1.1 Header -->
    <div class="app-header header-shadow relative z-40">
      <Header onMenuClick={openSheet} />
    </div>
    <!-- 1.2 Layout body -->
    <div class="app-layout-body bg-sidebar flex-1 overflow-hidden">
      <div class="mx-auto flex h-full max-w-7xl">
        <!-- 1.2.1 Aside and nav -->
        <!-- If media<xl, aside and nav show as one sidebar -->
        <!-- If media>=xl, aside and nav show as standalone cards -->
        <div class="aside-container hidden md:flex">
          <div class="aside-item border-border border-r xl:shadow-sm">
            <ConfigAside class="" />
          </div>
          <div class="aside-item xl:shadow-sm">
            <MainNav class="w-60 " />
          </div>
        </div>
        <!-- 1.2.2 If media<md, aside and nav show as sheet  -->
        <Sheet.Root bind:open={isSheetOpen}>
          <Sheet.Content side="left" class="max-width-85vw">
            <div class="flex h-full">
              <ConfigAside onNavigate={closeSheet} class="border-border border-r" />
              <MainNav class="flex-1" />
            </div>
          </Sheet.Content>
        </Sheet.Root>
        <!-- 1.2.3 Content -->
        <main class="app-content flex-1 overflow-auto px-2.5">
          {@render children()}
        </main>
      </div>
    </div>
  </div>
{:else}
  <!-- 2. Error page -->
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

<style>
  .header-shadow {
    box-shadow: 0 0 8px rgba(0, 0, 0, 0.2);
  }
  .aside-container {
    box-shadow: 0 0 8px rgba(0, 0, 0, 0.1);
    background-color: (--background);
    overflow: hidden;
  }
  @media (min-width: 1280px) {
    .aside-container {
      margin-top: 0.5rem;
      gap: 0.5rem;
      box-shadow: none;
      overflow: visible;
      background-color: transparent;
    }
    .aside-item {
      overflow: hidden;
      border: none;
      border-radius: var(--radius);
      background-color: var(--color-background);
      align-self: flex-start;
      min-height: 20rem;
    }
  }
</style>

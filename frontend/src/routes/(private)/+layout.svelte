<script lang="ts">
  import { browser } from "$app/environment";
  import { invalidateAll } from "$app/navigation";
  import ConfigAside from "$lib/components/aside/ConfigAside.svelte";
  import { Header } from "$lib/components/header";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import * as Sheet from "$lib/components/ui/sheet";
  import { HeaderContext, NavContext } from "$lib/slotcontext.svelte.js";
  import { screen } from "$lib/use/screen.svelte";
  import { cn } from "$lib/utils.js";

  // props
  let { data, children } = $props();

  // nav context
  NavContext.init();
  // header context
  HeaderContext.init();

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

  // Auto close sheet if window get resized
  $effect(() => {
    if (browser && isSheetOpen && screen.isMD) {
      closeSheet();
    }
  });
</script>

<svelte:window />
{#if data.success}
  <!-- 1. Main layout -->
  <div class="app-grid dotbg h-screen w-full overflow-hidden">
    <!-- 1.1 Header -->
    <div class={cn("area-header", screen.isMDtoXL ? "border-border border-l" : "header-shadow")}>
      <Header onMenuClick={openSheet} />
    </div>

    <!-- 1.2 Desktop Aside -->
    <div class="area-aside hidden shrink-0 md:flex">
      <div class="aside-container flex h-full shrink-0">
        <div class={cn("aside-item bg-card border-border border-r", screen.isXL && "neushadow")}>
          <ConfigAside class="pt-1 xl:pt-0" />
        </div>
        {#if NavContext.snippet}
          <div class={cn("aside-item bg-card w-50 pt-1 xl:pt-0", screen.isXL && "neushadow w-60")}>
            {@render NavContext.snippet()}
          </div>
        {/if}
      </div>
    </div>

    <!-- 1.3 Layout body -->
    <div class={cn("area-main flex flex-col overflow-hidden", screen.isMDtoXL && "area-shadow")}>
      <div class={cn("flex flex-1 flex-col overflow-auto")}>
        <main class="app-content flex h-full min-w-0 flex-col">
          {@render children()}
        </main>
      </div>
    </div>

    <!-- 1.4 Sheet for < md -->
    <Sheet.Root bind:open={isSheetOpen}>
      <Sheet.Content side="left" class="aside-shadow bg-card max-w-sm border-none p-0">
        <div class="flex h-full">
          <ConfigAside class="border-border border-r" />
          <div class="flex-1">
            {@render NavContext.snippet?.()}
          </div>
        </div>
      </Sheet.Content>
    </Sheet.Root>
  </div>
{:else}
  <!-- 2. Error page -->
  <div class="dotbg bg-background fixed inset-0 z-50 flex items-center justify-center">
    <Card.Root class="neushadow animate-in fade-in-0 zoom-in-95 mx-4 w-full max-w-md border-none">
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
  .app-grid {
    display: grid;
    grid-template-rows: auto 1fr;
    grid-template-columns: 1fr;
    grid-template-areas:
      "header"
      "main";
  }
  @media (min-width: 768px) {
    .app-grid {
      grid-template-columns: auto 1fr;
      grid-template-areas:
        "aside header"
        "aside main";
    }
  }
  @media (min-width: 1280px) {
    .app-grid {
      grid-template-areas:
        "header header"
        "aside main";
    }
  }

  .area-header {
    grid-area: header;
    z-index: 40;
  }
  @media (min-width: 768px) {
    .area-aside {
      z-index: 30;
    }
  }
  .area-aside {
    grid-area: aside;
  }
  .area-main {
    grid-area: main;
    z-index: 0;
  }

  .aside-container {
    background-color: var(--background);
    overflow: hidden;
  }
  @media (min-width: 1280px) {
    .aside-container {
      margin-top: 1rem;
      margin-bottom: 1rem;
      margin-left: 1rem;
      height: calc(100% - 2rem);
      gap: 1rem;
      box-shadow: none;
      overflow: visible;
      background-color: transparent;
    }
    .aside-item {
      overflow: hidden;
      border: none;
      border-radius: calc(var(--radius) + 2px);
      height: 100%;
      min-height: 20rem;
    }
  }
</style>

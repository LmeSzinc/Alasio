<script lang="ts">
  import { browser } from "$app/environment";
  import { invalidateAll } from "$app/navigation";
  import ConfigAside from "$lib/components/aside/ConfigAside.svelte";
  import { Header } from "$lib/components/header";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import * as Sheet from "$lib/components/ui/sheet";
  import { ThemeToggle } from "$lib/components/ui/theme/index.js";
  import { initNavContext } from "$lib/navcontext.svelte";
  import { cn } from "$lib/utils.js";
  // props
  let { data, children } = $props();

  // nav context
  const slots = initNavContext();

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
  let innerWidth = $state(0);
  $effect(() => {
    // 768 is media=md
    if (browser && isSheetOpen && innerWidth >= 768) {
      closeSheet();
    }
  });
  // I just don't know why "xl:neushadow" doesn't work, so just adding class name dymacally
  const isXlScreen = $derived(innerWidth >= 1280);
</script>

<svelte:window bind:innerWidth />
<ThemeToggle class="absolute right-5 bottom-5 z-50"></ThemeToggle>
{#if data.success}
  <!-- 1. Main layout -->
  <div class="app-container flex h-screen w-full flex-col">
    <!-- 1.1 Header -->
    <div class="app-header header-shadow relative z-40">
      <Header onMenuClick={openSheet} />
    </div>
    <!-- 1.2 Layout body -->
    <div class="app-layout-body flex-1 overflow-hidden">
      <div class="mx-auto flex h-full max-w-7xl">
        <!-- 1.2.1 Aside and nav -->
        <!-- If media<xl, aside and nav show as one sidebar -->
        <!-- If media>=xl, aside and nav show as standalone cards -->
        <div class="aside-container hidden md:flex">
          <div class={cn("aside-item bg-card border-border border-r", isXlScreen && "neushadow")}>
            <ConfigAside class="pt-1 xl:pt-0" />
          </div>
          {#if slots.nav}
            <div class={cn("aside-item bg-card w-60 pt-1 xl:pt-0", isXlScreen && "neushadow")}>
              {@render slots.nav()}
            </div>
          {/if}
        </div>
        <!-- 1.2.2 If media<md, aside and nav show as sheet  -->
        <Sheet.Root bind:open={isSheetOpen}>
          <Sheet.Content side="left" class="max-width-85vw">
            <div class="flex h-full">
              <ConfigAside onNavigate={closeSheet} class="border-border border-r" />
              <div class="flex-1">
                {#if slots.nav}
                  {@render slots.nav()}
                {/if}
              </div>
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
      margin-top: 1rem;
      gap: 1rem;
      box-shadow: none;
      overflow: visible;
      background-color: transparent;
    }
    .aside-item {
      overflow: hidden;
      border: none;
      border-radius: calc(var(--radius) + 2px);
      align-self: flex-start;
      min-height: 20rem;
    }
  }
</style>

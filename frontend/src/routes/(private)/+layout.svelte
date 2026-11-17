<script lang="ts">
  import { browser } from "$app/environment";
  import { invalidateAll } from "$app/navigation";
  import ConfigAside from "$lib/components/aside/ConfigAside.svelte";
  import { Header } from "$lib/components/header";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import * as Sheet from "$lib/components/ui/sheet";
  import UpdateAvailableToast from "$lib/components/update/UpdateAvailableToast.svelte";
  import { HeaderContext, NavContext } from "$lib/slotcontext.svelte.js";
  import { screen } from "$lib/use/screen.svelte";
  import { cn } from "$lib/utils.js";
  import { DragRegionContext } from "$lib/window/dragRegion.svelte";
  import { frame } from "$lib/window/frame.svelte";
  import { breakpointOf, defaultRegion, sheetRegion } from "$lib/window/regions";

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

  // Declared by the layout, overridden by pages that put controls in the
  // header; svelte drops the page layer when the page is destroyed, so a page
  // change restores this one without watching the route. The insets are read
  // when the stack is folded (inside the reporter's effect), so they follow the
  // breakpoint and the controls width.
  DragRegionContext.use((inherited) =>
    isSheetOpen
      ? sheetRegion(frame.controls.width)(inherited)
      : defaultRegion(breakpointOf(screen.width), frame.controls.width)(inherited),
  );

  // Auto close sheet if window get resized
  $effect(() => {
    if (browser && isSheetOpen && screen.isMD) {
      closeSheet();
    }
  });

  // Avoid double scrollbar when dropdown expanded
  // where scroll bar 1 is page content, scrollbar 2 is body as bits-ui dropdown is mounted on body
  $effect(() => {
    document.body.style.overflow = "hidden";
    document.body.style.height = "100%";
    document.documentElement.style.height = "100%";
    return () => {
      document.body.style.overflow = "";
      document.body.style.height = "";
      document.documentElement.style.height = "";
    };
  });
</script>

<svelte:window />
{#if data.success}
  <!-- 1. Main layout -->
  <div class="app-grid dotbg h-screen w-full overflow-hidden">
    <!-- 1.1 Header -->
    <!-- md..xl: the header starts at the right border of the nav column, so that
         border (1.2) is the seam; a border of this box would draw the same line
         twice, and over the page background instead of the card -->
    <div class={cn("area-header", !screen.isMDtoXL && "header-shadow")}>
      <Header onMenuClick={openSheet} />
    </div>

    <!-- 1.2 Desktop Aside -->
    <div class="area-aside hidden h-full min-h-0 shrink-0 md:flex">
      <div class={cn("aside-item bg-card border-border border-r", screen.isXL && "neushadow")}>
        <ConfigAside class="pt-1 xl:pt-0" />
      </div>
      {#if NavContext.snippet}
        <!-- The right border is the divider of the nav column in md..xl, the
             counterpart of the config column's one above: both are card
             borders, so the two dividers of the column look the same. The xl
             block drops it together with the other card borders -->
        <div class={cn("aside-item bg-card border-border w-50 border-r xl:pt-0", screen.isXL && "neushadow w-60")}>
          {@render NavContext.snippet()}
        </div>
      {/if}
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
    <!-- The sheet covers the app header, so its top band is the same h-12 band
         the header draws. The close button is not centered in that band: a
         glyph centered in its own box sits above the optical center of the text
         beside it, because text ink is placed from the baseline and leans below
         its line box (measured on a 14px line: text ink 19..32.5, its box
         14..34). The band center would be top-2 (button center 24); top-2.5
         puts the button center at 26, on the ink center of the title row.
         The insets follow the same rule on both sides: the painted cross is
         inset about 3.5px inside its 16px glyph box, so the default right-4
         (16px) leaves 27.3px of ink to the right edge against 26.3px above.
         right-2.5 (10px) evens them out, 26.3px above against 25.7px right. -->
    <Sheet.Root bind:open={isSheetOpen}>
      <Sheet.Content side="left" closeClass="top-2.5 right-2.5" class="aside-shadow bg-card max-w-sm border-none p-0">
        <div class="flex h-full">
          <ConfigAside class="border-border border-r" />
          <div class="flex-1">
            {@render NavContext.snippet?.()}
          </div>
        </div>
      </Sheet.Content>
    </Sheet.Root>

    <!-- 1.5 Update available toast: its Update topic subscription must be
         alive on every page (a check completes while the user is anywhere) -->
    <UpdateAvailableToast />
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
    .aside-item {
      height: 100%;
      overflow: hidden;
    }
  }
  .area-aside {
    grid-area: aside;
    min-height: 0;
    background-color: var(--background);
  }
  .area-main {
    grid-area: main;
    z-index: 0;
    min-height: 0;
  }

  @media (min-width: 1280px) {
    .area-aside {
      margin-top: 1rem;
      margin-bottom: 1rem;
      margin-left: 1rem;
      height: calc(100% - 2rem);
      gap: 1rem;
      box-shadow: none;
      background-color: transparent;
    }
    .aside-item {
      border: none;
      border-radius: calc(var(--radius) + 2px);
    }
  }
</style>

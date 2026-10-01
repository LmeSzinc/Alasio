<script lang="ts">
  import { dev } from "$app/environment";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import SafeBold from "$lib/components/aside/SafeBold.svelte";
  import { SidebarContent, SidebarHeader, SidebarRow, SidebarTitle } from "$lib/components/sidebar-accordion";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { t } from "$lib/i18n";
  import { HeaderContext } from "$lib/slotcontext.svelte";
  import { cn } from "$lib/utils";

  // --- Props Definition (Svelte 5 Runes) ---
  type $props = {
    class?: string;
  };

  let { class: className }: $props = $props();

  // --- Navigation Items ---
  const alasioNavItems = $derived([
    { path: "/dev/config", name: t.ConfigScan.ConfigManager() },
    { path: "/dev/mod", name: t.Mod.Title() },
    { path: "/dev/setting", name: t.DevTool.AlasioSettings() },
  ]);
  const devNavItems = $derived([
    { path: "/dev/assets", name: t.AssetManager.AssetManager() },
    { path: "/dev/compat", name: "Browser Compatibility" },
  ]);
  const debugNavItems = $derived(
    // /dev/debug/* routes are dropped from the production build by the
    // svelte-drop-dev-page plugin, so only show them in development
    dev
      ? [
          { path: "/dev/debug/ws", name: t.WebsocketTest.Title() },
          { path: "/dev/debug/modmanager", name: "Mod Manager" },
          { path: "/dev/debug/workerstatus", name: "Worker Status" },
          { path: "/dev/debug/restartstatus", name: "Restart Status" },
          { path: "/dev/debug/scheduler", name: "Scheduler" },
          { path: "/dev/debug/preview", name: "Preview" },
          { path: "/dev/debug/dashboard", name: "Dashboard" },
          { path: "/dev/debug/dashboardgroup", name: "Dashboard Group" },
          { path: "/dev/debug/log", name: "Log Viewer" },
          { path: "/dev/debug/configdisplay", name: "Config Display" },
          { path: "/dev/debug/arginput", name: "Arg Input" },
        ]
      : [],
  );

  // --- Derived State ---
  const currentPath = $derived(page.url.pathname);
  function matchPath(path: string) {
    return currentPath === path || currentPath.startsWith(path + "/");
  }

  // --- Event Handlers ---
  async function handleNavClick(path: string) {
    await goto(path);
  }

  // header snippet
  const displayHeader = $derived.by(() => {
    for (const item of alasioNavItems) {
      if (matchPath(item.path)) return item.name;
    }
    for (const item of devNavItems) {
      if (matchPath(item.path)) return item.name;
    }
    for (const item of debugNavItems) {
      if (matchPath(item.path)) return item.name;
    }
    return currentPath;
  });
  HeaderContext.use(header);
</script>

{#snippet header()}
  <h1 class="w-full flex-1 text-center text-lg">{displayHeader}</h1>
{/snippet}

{#snippet navSection(title: string, items: typeof devNavItems, header = false)}
  <!-- This section's border-t aligns with AppHeader bottom:
       aside pt-2 (8px) + header (line-height 28px + py-1.5 12px = 40px) = 48px = h-12 -->
  <div class="flex flex-col">
    {#if header}
      <SidebarHeader>{title}</SidebarHeader>
    {:else}
      <SidebarTitle>{title}</SidebarTitle>
    {/if}
    <div class="border-border mx-1 border-t"></div>
    <SidebarContent>
      {#each items as item (item.path)}
        {@const isActive = matchPath(item.path)}
        <SidebarRow active={isActive} onclick={() => handleNavClick(item.path)}>
          <SafeBold active={isActive} text={item.name} normalClass="font-medium" />
        </SidebarRow>
      {/each}
    </SidebarContent>
  </div>
{/snippet}

<ScrollArea class="h-full w-full">
  <aside class={cn("w-full pt-2 pb-4", className)} role="navigation" aria-label="Main navigation">
    {@render navSection(t.DevTool.AlasioTool(), alasioNavItems, true)}
    {@render navSection(t.DevTool.DevTool(), devNavItems)}
    {#if dev}
      {@render navSection(t.DevTool.DebugTool(), debugNavItems)}
    {/if}
  </aside>
</ScrollArea>

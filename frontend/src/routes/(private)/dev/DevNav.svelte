<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import SafeBold from "$lib/components/aside/SafeBold.svelte";
  import { SidebarContent, SidebarHeader, SidebarRow, SidebarTitle } from "$lib/components/sidebar-accordion";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { devNavGroups, isNavPath } from "$lib/dev/nav";
  import { cn } from "$lib/utils";

  // --- Props Definition (Svelte 5 Runes) ---
  type $props = {
    class?: string;
  };

  let { class: className }: $props = $props();

  // --- Derived State ---
  const groups = $derived(devNavGroups());
  const currentPath = $derived(page.url.pathname);
  function matchPath(path: string) {
    return isNavPath(currentPath, path);
  }

  // --- Event Handlers ---
  async function handleNavClick(path: string) {
    await goto(path);
  }
</script>

{#snippet navSection(group: (typeof groups)[number])}
  <!-- This section's border-t aligns with AppHeader bottom:
       aside pt-2 (8px) + header (line-height 28px + py-1.5 12px = 40px) = 48px = h-12 -->
  <div class="flex flex-col">
    {#if group.lead}
      <SidebarHeader>{group.title}</SidebarHeader>
    {:else}
      <SidebarTitle>{group.title}</SidebarTitle>
    {/if}
    <div class="mx-1 border-t border-border"></div>
    <SidebarContent>
      {#each group.items as item (item.path)}
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
    {#each groups as group (group.id)}
      {#if group.items.length > 0}
        {@render navSection(group)}
      {/if}
    {/each}
  </aside>
</ScrollArea>

<script lang="ts">
  import {
    Breadcrumb,
    BreadcrumbItem,
    BreadcrumbLink,
    BreadcrumbList,
    BreadcrumbPage,
    BreadcrumbSeparator,
  } from "$lib/components/ui/breadcrumb";
  import { FolderOpen } from "@lucide/svelte";
  import { cn } from "$lib/utils";

  let {
    mod_path_assets,
    path,
    onNavigate,
    class: className,
  }: {
    mod_path_assets: string;
    path: string;
    onNavigate?: (path: string) => void;
    class?: string;
  } = $props();

  interface BreadcrumbSegment {
    name: string;
    path: string;
    isLast: boolean;
  }

  // Parse path into breadcrumb array
  const breadcrumbs: BreadcrumbSegment[] = $derived.by(() => {
    const result: BreadcrumbSegment[] = [];

    // If current path is empty or equals mod_path_assets, only show root directory
    // Display full mod_path_assets so developers know the real path
    if (!path || path === mod_path_assets) {
      return [{ name: mod_path_assets, path: mod_path_assets, isLast: true }];
    }

    // Ensure currentPath starts with mod_path_assets
    if (!path.startsWith(mod_path_assets)) {
      return [];
    }

    // Add root directory (display full mod_path_assets)
    result.push({ name: mod_path_assets, path: mod_path_assets, isLast: false });

    // Get the path portion relative to mod_path_assets
    const relativePath = path.slice(mod_path_assets.length);
    const segments = relativePath.split("/").filter((s) => s.length > 0);

    // Build complete path for each segment
    let accumulatedPath = mod_path_assets;
    segments.forEach((segment, index) => {
      accumulatedPath = `${accumulatedPath}/${segment}`;
      result.push({
        name: segment,
        path: accumulatedPath,
        isLast: index === segments.length - 1,
      });
    });

    return result;
  });

  function handleBreadcrumbClick(path: string): void {
    onNavigate?.(path);
  }
</script>

<Breadcrumb class={cn("px-4 py-2", className)}>
  <BreadcrumbList>
    <div class="flex items-center gap-2">
      <FolderOpen class="text-muted-foreground h-4 w-4" />

      {#each breadcrumbs as crumb, index}
        {#if index > 0}
          <BreadcrumbSeparator />
        {/if}

        <BreadcrumbItem>
          {#if crumb.isLast}
            <BreadcrumbPage>{crumb.name}</BreadcrumbPage>
          {:else}
            <BreadcrumbLink class="cursor-pointer" onclick={() => handleBreadcrumbClick(crumb.path)}>
              {crumb.name}
            </BreadcrumbLink>
          {/if}
        </BreadcrumbItem>
      {/each}
    </div>
  </BreadcrumbList>
</Breadcrumb>

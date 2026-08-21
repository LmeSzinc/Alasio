<script lang="ts">
  import { goto } from "$app/navigation";
  import { LogOut } from "@lucide/svelte";
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";

  // Display this tool as an arg row
  // $derived so name/help follow the current display language
  const data = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "ReturnToLogin",
    dt: "static",
    value: null,
    name: t.DevTool.ReturnToLogin(),
    help: t.DevTool.ReturnToLoginHelp(),
  }));
</script>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike {data}>
    {#snippet InputSnippet()}
      <Button onclick={() => goto("/auth")} variant="outline" class="w-full">
        <LogOut class="mr-2 h-4 w-4" />
        {t.DevTool.ReturnToLogin()}
      </Button>
    {/snippet}
  </LayoutHorizontalLike>
</div>

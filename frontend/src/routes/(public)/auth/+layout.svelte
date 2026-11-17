<script lang="ts">
  import AppHeader from "$lib/components/header/AppHeader.svelte";
  import { t } from "$lib/i18n";
  import { DragRegionContext } from "$lib/window/dragRegion.svelte";
  import { authRegion } from "$lib/window/regions";

  let { children } = $props();

  // The public pages have no aside and no menu button: everything left of the
  // language selector can drag the window.
  DragRegionContext.use(authRegion);
</script>

<div class="flex min-h-screen flex-col">
  <AppHeader>
    <h1 class="w-full text-center text-lg">{t.Auth.Login()}</h1>
  </AppHeader>
  <div class="dotbg flex-1">
    {@render children()}
  </div>
</div>

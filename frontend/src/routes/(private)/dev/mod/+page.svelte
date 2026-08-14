<script lang="ts">
  import { t } from "$lib/i18n";
  import ModManager from "./ModManager.svelte";
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">{t.Mod.Title()}</h1>
  </header>

  <ModManager class="grow" />
</div>

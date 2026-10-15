<script lang="ts">
  import { t } from "$lib/i18n";
  import ModManager from "./ModManager.svelte";
</script>

<div class="container mx-auto flex w-full flex-col gap-4 p-4 pb-20">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">{t.Mod.Updater()}</h1>
  </header>

  <ModManager />
</div>

<script lang="ts">
  import { t } from "$lib/i18n";
  import RestartBackendCard from "./RestartBackendCard.svelte";
  import ReturnToLoginCard from "./ReturnToLoginCard.svelte";
  import WindowControlsAvoidCard from "./WindowControlsAvoidCard.svelte";
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">{t.DevTool.SystemTool()}</h1>
  </header>

  <div class="flex flex-wrap gap-4">
    <RestartBackendCard />
    <ReturnToLoginCard />
    <WindowControlsAvoidCard />
  </div>
</div>

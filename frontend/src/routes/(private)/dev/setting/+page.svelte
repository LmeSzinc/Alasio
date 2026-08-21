<script lang="ts">
  import ArgGroupCard from "$lib/components/arg/ArgGroupCard.svelte";
  import { t } from "$lib/i18n";
  import DisplayTool from "./DisplayTool.svelte";
  import RestartBackendTool from "./RestartBackendTool.svelte";
  import ReturnToLoginTool from "./ReturnToLoginTool.svelte";
  import WindowControlsAvoidTool from "./WindowControlsAvoidTool.svelte";
</script>

<div class="container mx-auto h-full w-full space-y-4 overflow-auto p-4">
  <ArgGroupCard title={t.DevTool.Display()} class="max-w-180">
    <DisplayTool />
  </ArgGroupCard>
  <ArgGroupCard title={t.DevTool.SystemTool()} class="max-w-180">
    <RestartBackendTool />
    <ReturnToLoginTool />
    <WindowControlsAvoidTool />
  </ArgGroupCard>
</div>

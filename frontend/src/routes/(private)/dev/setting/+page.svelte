<script lang="ts">
  import ArgGroupCard from "$lib/components/arg/ArgGroupCard.svelte";
  import { t } from "$lib/i18n";
  import { cardWidth } from "$lib/use/cardWidth.svelte";
  import { elementSize } from "$lib/use/size.svelte";
  import DisplayTool from "./DisplayTool.svelte";
  import RestartBackendTool from "./RestartBackendTool.svelte";
  import RestartStatus from "./RestartStatus.svelte";
  import ReturnToLoginTool from "./ReturnToLoginTool.svelte";
  import WindowControlsAvoidTool from "./WindowControlsAvoidTool.svelte";

  // the width of the cards follows the config pages, see cardWidth()
  let containerSize = $state({ width: 0, height: 0 });
  const cardClass = $derived(cardWidth(containerSize.width));
</script>

<div class="container mx-auto w-full space-y-4 p-4 pb-20" use:elementSize={containerSize}>
  <ArgGroupCard title={t.DevTool.Display()} class={cardClass}>
    <DisplayTool />
  </ArgGroupCard>
  <ArgGroupCard title={t.DevTool.SystemTool()} class={cardClass}>
    <RestartBackendTool />
    <!-- Status of the restart in progress (phase + configs still running) -->
    <RestartStatus />
    <ReturnToLoginTool />
    <WindowControlsAvoidTool />
  </ArgGroupCard>
</div>

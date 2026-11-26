<script lang="ts">
  import Cog from "@lucide/svelte/icons/cog";
  import LockKeyhole from "@lucide/svelte/icons/lock-keyhole";
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import { Button } from "$lib/components/ui/button";
  import * as Dialog from "$lib/components/ui/dialog";
  import { t } from "$lib/i18n";
  import Enable from "../arginput/Enable.svelte";
  import Input from "../arginput/Input.svelte";
  import Static from "../arginput/Static.svelte";
  import CardHeaderButton from "./CardHeaderButton.svelte";
  import { type CardData, type InfoData, type InputProps, i18nTextToOneLine } from "./utils.svelte";

  type Props = {
    cardData: CardData;
    handleEdit: InputProps["handleEdit"];
    handleReset: InputProps["handleReset"];
    handleCardReset?: (data: InfoData) => void;
    /**
     * True when the card holds an arg behind the advanced toggle: without one
     * the toggle is not rendered at all.
     */
    hasAdvanced?: boolean;
    /** Open state of the advanced settings of the card */
    isAdvanced?: boolean;
    class?: string;
  };
  let {
    cardData = $bindable(),
    handleEdit,
    handleReset,
    handleCardReset,
    hasAdvanced = false,
    isAdvanced = $bindable(false),
    class: className,
  }: Props = $props();

  // The open toggle stays visible on its own: the card then shows rows the
  // user has to be able to hide again. Without an advanced arg there is no
  // toggle, so nothing keeps the card in the opened state either.
  const isAdvancedShown = $derived(isAdvanced && hasAdvanced);

  const SchedulerEnable = $derived(cardData?.Scheduler?.Enable);
  const SchedulerNextRun = $derived(cardData?.Scheduler?.NextRun);

  let dialogOpen = $state(false);

  const svgW = 144;
  const svgR = 16;
  const svgH = svgR * 3;
  const svgD = `M 0 0
A ${svgR} ${svgR} 0 0 1 ${svgR} ${svgR} 
A ${svgR} ${svgR} 0 0 0 ${svgR * 2} ${svgR * 2} 
L ${svgW - svgR} ${svgR * 2} 
A ${svgR} ${svgR} 0 0 1 ${svgW} ${svgH}
L ${svgW} 0
Z`;
</script>

<!-- rounded-xl to be the same as shadcn Card component -->
<div class="pointer-events-none absolute inset-0 z-10 overflow-hidden rounded-xl">
  {#if SchedulerEnable}
    {@const enable = !!SchedulerEnable?.value}
    <!-- SVG deco tag -->
    <svg viewBox="0 0 {svgW} {svgH}" width={svgW} height={svgH} class="absolute top-0 right-0">
      <defs>
        <!-- define diagonal pattern -->
        <!-- patternUnits="userSpaceOnUse" to keep pattern size fixed -->
        <!-- width and height determine the distance between diagonal lines -->
        <pattern id="diagonalHatch" patternUnits="userSpaceOnUse" width="10" height="10">
          <!-- draw a white diagonal line -->
          <path
            d="M-1,1 l2,-2
               M0,10 l10,-10
               M9,11 l2,-2"
            stroke-width="1"
            stroke-opacity="0.5"
            class="stroke-card"
          />
        </pattern>
      </defs>
      <path d={svgD} class={enable ? "fill-primary" : "fill-muted-foreground/30"} />
      <path d={svgD} fill="url(#diagonalHatch)" />
    </svg>
  {/if}
  <div class="absolute top-0 right-0 flex flex-col items-end gap-1">
    <!-- Enable button row -->
    <div class="flex h-8 items-center justify-center gap-x-2">
      <!-- Card-level buttons - visible on card hover, and always while the
           advanced settings are open -->
      <!-- Reset and Advanced sit right next to each other, so only their own
           paddings separate them; the gap to the Enable badge is wider on
           purpose. The header buttons carry 4px less padding per side than a
           plain button, and this row gives back the same 4px here, so the
           rightmost label keeps its distance to the badge. -->
      <div class="flex items-center">
        <!-- Reset button, visible on card hover -->
        <CardHeaderButton icon={RotateCcw} label={t.Input.GroupResetTitle()} onclick={() => (dialogOpen = true)} />
        {#if hasAdvanced}
          <!-- Advanced settings: appears on card hover, and stays on screen
               once opened so the rows it reveals can be closed again. The open
               state is primary text with a standing rule instead of a filled
               pill: the Enable badge next to it already owns that fill, and two
               primary blocks on one header would compete for weight. -->
          <CardHeaderButton
            icon={Cog}
            label={t.Input.Advanced()}
            active={isAdvancedShown}
            toggle
            onclick={() => (isAdvanced = !isAdvanced)}
          />
        {/if}
      </div>
      {#if SchedulerEnable}
        <div class="flex h-8 w-32 items-center justify-center">
          {#if SchedulerEnable?.dt === "static"}
            <div class="flex items-center">
              <LockKeyhole class="h-4 w-4 text-white" />
              <Static
                bind:data={cardData.Scheduler.Enable}
                class="pointer-events-auto h-5 px-1 py-0 text-sm"
                isRevtColor
              />
            </div>
          {:else}
            <Enable
              bind:data={cardData.Scheduler.Enable}
              {handleEdit}
              {handleReset}
              class="pointer-events-auto mx-auto h-5 w-auto text-sm"
              isRevtColor
            />
          {/if}
        </div>
      {:else}
        <!-- placeholder to keep the right position -->
        <div class="h-8 w-4"></div>
      {/if}
    </div>
    <!-- NextRun -->
    {#if SchedulerNextRun}
      <div class="mr-6 flex w-50 items-center justify-center">
        <Input
          bind:data={cardData.Scheduler.NextRun}
          {handleEdit}
          {handleReset}
          class="pointer-events-auto w-full text-sm"
          isDesc
        />
      </div>
    {/if}
  </div>
</div>

<!-- Dialog for resetting the group -->
<Dialog.Root bind:open={dialogOpen}>
  <Dialog.Content>
    <Dialog.Header>
      <Dialog.Title>{t.Input.GroupResetTitle()}</Dialog.Title>
      <Dialog.Description>
        {t.Input.GroupResetDesc({ name: i18nTextToOneLine(cardData?._info?.name) || "UnknownGroupName" })}
      </Dialog.Description>
    </Dialog.Header>
    <Dialog.Footer>
      <Button variant="outline" onclick={() => (dialogOpen = false)}>
        {t.ConfigScan.Cancel()}
      </Button>
      <Button
        variant="destructive"
        onclick={() => {
          handleCardReset?.(cardData?._info);
          dialogOpen = false;
        }}
      >
        {t.Input.GroupResetConfirm()}
      </Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>

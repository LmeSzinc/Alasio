<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Dialog from "$lib/components/ui/dialog";
  import { t } from "$lib/i18n";
  import { LockKeyhole, RotateCcw } from "@lucide/svelte";
  import Enable from "../arginput/Enable.svelte";
  import Input from "../arginput/Input.svelte";
  import Static from "../arginput/Static.svelte";
  import type { CardData, InputProps } from "./utils.svelte";

  type Props = {
    cardData: CardData;
    handleEdit: InputProps["handleEdit"];
    handleReset: InputProps["handleReset"];
    handleGroupReset?: (data: any) => void;
    class?: string;
  };
  let { cardData = $bindable(), handleEdit, handleReset, handleGroupReset, class: className }: Props = $props();

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

{#if SchedulerEnable}
  {@const enable = !!SchedulerEnable?.value}
  <!-- rounded-xl to be the same as shadcn Card component -->
  <div class="pointer-events-none absolute inset-0 z-10 overflow-hidden rounded-xl">
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
    <div class="absolute top-0 right-0 flex flex-col items-end gap-1">
      <!-- Enable button -->
      <div class="flex h-8 items-center justify-center gap-x-1">
        <!-- Reset button -->
        <Button
          variant="ghost"
          size="icon"
          class="text-muted-foreground pointer-events-auto h-6 w-6"
          onclick={() => (dialogOpen = true)}
        >
          <RotateCcw class="size-3.5" />
        </Button>

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
{/if}

<!-- Dialog for resetting the group -->
<Dialog.Root bind:open={dialogOpen}>
  <Dialog.Content>
    <Dialog.Header>
      <Dialog.Title>{t.Input.GroupResetTitle()}</Dialog.Title>
      <Dialog.Description>
        {t.Input.GroupResetDesc({ name: cardData?._info?.name || "UnknownGroupName" })}
      </Dialog.Description>
    </Dialog.Header>
    <Dialog.Footer>
      <Button variant="outline" onclick={() => (dialogOpen = false)}>
        {t.ConfigScan.Cancel()}
      </Button>
      <Button
        variant="destructive"
        onclick={() => {
          handleGroupReset?.(cardData._info);
          dialogOpen = false;
        }}
      >
        {t.Input.GroupResetConfirm()}
      </Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>

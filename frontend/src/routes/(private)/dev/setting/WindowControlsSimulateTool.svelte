<script lang="ts">
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import * as Select from "$lib/components/ui/select";
  import { t } from "$lib/i18n";
  import { isElectronSession } from "$lib/use/useElectronEnv.svelte";
  import { dragRegionOutline, setDragRegionOutline } from "$lib/window/dragRegion.svelte";
  import { type WindowControlsSimulateMode, frame } from "$lib/window/frame.svelte";

  // The window controls are drawn by the electron host; the frontend only
  // reserves their width (see `$lib/window/frame.svelte`). Outside electron
  // there is no host to measure, so the mode decides what the width falls back
  // to: `auto` follows the real value inside the client and simulates it in a
  // browser that has been in the webapp before, `always` simulates everywhere
  // (a fresh dev profile), `never` shows the remote-browser layout.

  // $derived so labels follow the current display language
  const simulateOptions = $derived.by<{ value: WindowControlsSimulateMode; label: string }[]>(() => [
    { value: "auto", label: t.DevTool.SimulateAuto() },
    { value: "always", label: t.DevTool.SimulateAlways() },
    { value: "never", label: t.DevTool.SimulateNever() },
  ]);

  function onSimulateModeChange(value: string | undefined) {
    if (value === "auto" || value === "always" || value === "never") {
      frame.simulateMode = value;
    }
  }

  // Display this tool as an arg row
  // $derived so name/help follow the current display language
  const data = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "WindowControlsSimulate",
    dt: "static",
    value: frame.simulateMode,
    name: t.DevTool.WindowControlsSimulate(),
    help: t.DevTool.WindowControlsSimulateHelp(),
  }));

  const outlineData = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "DragRegionOutline",
    dt: "static",
    value: dragRegionOutline.enabled,
    name: t.DevTool.SimulateOutline(),
    help: t.DevTool.SimulateOutlineHelp(),
  }));
</script>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike {data}>
    {#snippet InputSnippet()}
      <Select.Root type="single" value={frame.simulateMode} onValueChange={onSimulateModeChange}>
        <Select.Trigger class="w-full">
          <span class="flex-1 truncate text-left">
            {simulateOptions.find((o) => o.value === frame.simulateMode)?.label ?? t.DevTool.SimulateAuto()}
          </span>
        </Select.Trigger>
        <Select.Content>
          <Select.Group>
            {#each simulateOptions as option (option.value)}
              <Select.Item value={option.value} label={option.label}>
                {option.label}
              </Select.Item>
            {/each}
          </Select.Group>
        </Select.Content>
      </Select.Root>
    {/snippet}
    {#snippet PlaceholderSnippet()}
      <span class="text-muted-foreground text-xs">
        {t.DevTool.SimulateSource({ width: frame.controls.width, source: frame.controls.source })}
        · {isElectronSession ? t.DevTool.SessionElectron() : t.DevTool.SessionBrowser()}
      </span>
    {/snippet}
  </LayoutHorizontalLike>
</div>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike data={outlineData}>
    {#snippet InputSnippet()}
      <Checkbox
        checked={dragRegionOutline.enabled}
        onCheckedChange={(checked) => setDragRegionOutline(checked === true)}
        class="size-4.5"
        iconStrokeWidth={3.5}
      />
    {/snippet}
  </LayoutHorizontalLike>
</div>

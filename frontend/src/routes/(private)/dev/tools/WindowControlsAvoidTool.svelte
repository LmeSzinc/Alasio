<script lang="ts">
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import * as Select from "$lib/components/ui/select";
  import { t } from "$lib/i18n";
  import { electronEnv, isElectronSession, type WindowControlsAvoidMode } from "$lib/use/useElectronEnv.svelte";

  // $derived so labels follow the current display language
  const avoidOptions = $derived.by<{ value: WindowControlsAvoidMode; label: string }[]>(() => [
    { value: "auto", label: t.DevTool.AvoidAuto() },
    { value: "always", label: t.DevTool.AvoidAlways() },
    { value: "never", label: t.DevTool.AvoidNever() },
  ]);

  function onAvoidModeChange(value: string | undefined) {
    if (value === "auto" || value === "always" || value === "never") {
      electronEnv.avoidMode = value;
    }
  }

  // Display this tool as an arg row
  // $derived so name/help follow the current display language
  const data = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "WindowControlsAvoid",
    dt: "static",
    value: electronEnv.avoidMode,
    name: t.DevTool.WindowControlsAvoid(),
    help: t.DevTool.WindowControlsAvoidHelp(),
  }));
</script>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike {data}>
    {#snippet InputSnippet()}
      <Select.Root type="single" value={electronEnv.avoidMode} onValueChange={onAvoidModeChange}>
        <Select.Trigger class="w-full">
          <span class="flex-1 truncate text-left">
            {avoidOptions.find((o) => o.value === electronEnv.avoidMode)?.label ?? t.DevTool.AvoidAuto()}
          </span>
        </Select.Trigger>
        <Select.Content>
          <Select.Group>
            {#each avoidOptions as option (option.value)}
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
        {isElectronSession ? t.DevTool.SessionElectron() : t.DevTool.SessionBrowser()}
      </span>
    {/snippet}
  </LayoutHorizontalLike>
</div>

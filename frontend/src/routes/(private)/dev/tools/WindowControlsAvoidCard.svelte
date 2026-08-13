<script lang="ts">
  import { PanelsTopLeft } from "@lucide/svelte";
  import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "$lib/components/ui/card";
  import * as Select from "$lib/components/ui/select";
  import { t } from "$lib/i18n";
  import { electronEnv, isElectronSession, type WindowControlsAvoidMode } from "$lib/use/useElectronEnv.svelte";

  const avoidOptions: { value: WindowControlsAvoidMode; label: string }[] = [
    { value: "auto", label: t.DevTool.AvoidAuto() },
    { value: "always", label: t.DevTool.AvoidAlways() },
    { value: "never", label: t.DevTool.AvoidNever() },
  ];

  function onAvoidModeChange(value: string | undefined) {
    if (value === "auto" || value === "always" || value === "never") {
      electronEnv.avoidMode = value;
    }
  }
</script>

<!-- Window Controls Avoid Card (debugging: only affects UI layout) -->
<Card class="neushadow min-w-64 border-0">
  <CardHeader>
    <CardTitle class="flex items-center gap-2">
      <PanelsTopLeft class="h-5 w-5" />
      {t.DevTool.WindowControlsAvoid()}
    </CardTitle>
    <CardDescription>
      <span class="mt-1 block">
        {isElectronSession ? t.DevTool.SessionElectron() : t.DevTool.SessionBrowser()}
      </span>
    </CardDescription>
  </CardHeader>
  <CardContent>
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
  </CardContent>
</Card>

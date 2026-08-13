<script lang="ts">
  import { goto } from "$app/navigation";
  import { LogOut, PanelsTopLeft, Power } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button";
  import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "$lib/components/ui/card";
  import * as Select from "$lib/components/ui/select";
  import { t } from "$lib/i18n";
  import { electronEnv, isElectronSession, type WindowControlsAvoidMode } from "$lib/use/useElectronEnv.svelte";
  import { useTopic } from "$lib/ws";
  import RestartDialog from "./RestartDialog.svelte";

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const restartRpc = topicClient.rpc();

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

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">{t.DevTool.SystemTool()}</h1>
  </header>

  <div class="flex flex-wrap gap-4">
    <!-- Restart Backend Card -->
    <Card class="neushadow min-w-64 border-0">
      <CardHeader>
        <CardTitle class="flex items-center gap-2">
          <Power class="h-5 w-5" />
          {t.DevTool.RestartBackend()}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Button onclick={restartRpc.open} variant="destructive" class="w-full">
          <Power class="mr-2 h-4 w-4" />
          {t.DevTool.RestartBackend()}
        </Button>
      </CardContent>
    </Card>

    <!-- Return to Login Card -->
    <Card class="neushadow min-w-64 border-0">
      <CardHeader>
        <CardTitle class="flex items-center gap-2">
          <LogOut class="h-5 w-5" />
          {t.DevTool.ReturnToLogin()}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Button onclick={() => goto("/auth")} variant="outline" class="w-full">
          <LogOut class="mr-2 h-4 w-4" />
          {t.DevTool.ReturnToLogin()}
        </Button>
      </CardContent>
    </Card>

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
  </div>
</div>

<!-- Dialog -->
<RestartDialog rpc={restartRpc} />
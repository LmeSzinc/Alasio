<script lang="ts">
  import Languages from "@lucide/svelte/icons/languages";
  import SunIcon from "@lucide/svelte/icons/sun";
  import Select from "$lib/components/Select.svelte";
  import StartupCard from "$lib/components/StartupCard.svelte";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { setLanguage } from "$lib/i18n/state.svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
  // Failure hint only after an attempt actually failed: "failed" is
  // published by the main process when the attempt settles with an error
  // (nothing is shown while idle or while an attempt is running).
  const failed = $derived(sharedState.backendStatus === "failed");
  // Initial selection mirrors the persistent config values (may be 'system');
  // the UI display language/theme follows the derived values in shared state.
  let selectedLang = $state(sharedState.configLang);
  let selectedTheme = $state(sharedState.configTheme);

  const languages = $derived([
    { value: "system", label: t.Setup.FollowSystem() },
    { value: "zh-CN", label: "简体中文" },
    { value: "en-US", label: "English" },
    { value: "ja-JP", label: "日本語" },
    { value: "zh-TW", label: "繁體中文" },
    { value: "es-ES", label: "Español" },
  ]);

  const themes = $derived([
    { value: "system", label: t.Setup.FollowSystem() },
    { value: "light", label: t.Setup.Light() },
    { value: "dark", label: t.Setup.Dark() },
  ]);

  // Selection writes the config value immediately through IPC: the main
  // process AppState is the single source of truth, it broadcasts to the
  // backend once it is ready (no saveFirstTimeConfig anymore, the backend
  // self-bootstraps deploy.yaml). The bind:value on Select keeps the local
  // selection in sync; here we only trigger the side effects.
  function selectLang(value: string | undefined) {
    if (value === undefined) return;
    setLanguage(value);
  }

  function selectTheme(value: string | undefined) {
    if (value === undefined) return;
    window.electronAPI.setTheme(value);
  }

  async function handleStart() {
    // Values are already saved through the IPC on selection; starting the
    // backend persists them into deploy.yaml through the stdin contract.
    // On failure the page stays put and shows the hint below (the failure
    // flag is published through shared state by the main process).
    await window.electronAPI.startBackend();
  }
</script>

<StartupCard title="Alasio" desc={t.Setup.Welcome()}>
  <div class="flex w-full flex-col gap-2">
    <div class="flex items-center justify-between gap-6">
      <label for="setup-language" class="flex items-center gap-3 text-lg">
        <Languages class="h-4 w-4" strokeWidth={1.5} />
        {t.Setup.SelectLanguage()}
      </label>
      <div class="w-60">
        <Select id="setup-language" bind:value={selectedLang} options={languages} onValueChange={selectLang} />
      </div>
    </div>

    <div class="flex items-center justify-between gap-6">
      <label for="setup-theme" class="flex items-center gap-3 text-lg">
        <SunIcon class="h-4 w-4" strokeWidth={1.5} />
        {t.Setup.SelectTheme()}
      </label>
      <div class="w-60">
        <Select id="setup-theme" bind:value={selectedTheme} options={themes} onValueChange={selectTheme} />
      </div>
    </div>
  </div>

  <Button onclick={handleStart} size="lg" class="mt-16 h-14 w-48 rounded-xl text-xl font-semibold">
    {t.Setup.Start()}
  </Button>
  {#if failed}
    <p class="text-destructive mt-4 text-lg">{t.Error.BackendStartFailed()}</p>
  {/if}
</StartupCard>

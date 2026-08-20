<script lang="ts">
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { setLanguage } from "$lib/i18n/state.svelte";
  import { t } from "$lib/i18n";

  const sharedState = useSharedState();
  // Initial selection mirrors the persistent config values (may be 'system');
  // the UI display language/theme follows the derived values in shared state.
  let selectedLang = $state(sharedState.configLang);
  let selectedTheme = $state(sharedState.configTheme);

  const languages = $derived([
    { code: "system", name: t.Setup.FollowSystem() },
    { code: "zh-CN", name: "简体中文" },
    { code: "en-US", name: "English" },
    { code: "ja-JP", name: "日本語" },
    { code: "zh-TW", name: "繁體中文" },
    { code: "es-ES", name: "Español" },
  ]);

  const themes = $derived([
    { code: "system", name: t.Setup.FollowSystem() },
    { code: "light", name: t.Setup.Light() },
    { code: "dark", name: t.Setup.Dark() },
  ]);

  // Selection writes the config value immediately through IPC: the main
  // process AppState is the single source of truth, it broadcasts to the
  // backend once it is ready (no saveFirstTimeConfig anymore, the backend
  // self-bootstraps deploy.yaml).
  function selectLang(code: string) {
    selectedLang = code;
    setLanguage(code);
  }

  function selectTheme(code: string) {
    selectedTheme = code;
    window.electronAPI.setTheme(code);
  }

  async function handleStart() {
    // Values are already saved through the IPC on selection; starting the
    // backend persists them into deploy.yaml through the stdin contract.
    await window.electronAPI.startBackend();
  }
</script>

<div class="flex h-screen items-center justify-center bg-background text-foreground">
  <div class="w-[600px] rounded-xl bg-card border border-border p-12 shadow-lg">
    <h1 class="text-5xl font-bold mb-4">Alasio</h1>
    <p class="text-xl text-muted-foreground mb-12">{t.Setup.Welcome()}</p>

    <div class="mb-8">
      <label class="text-lg mb-4 block">
        {t.Setup.SelectLanguage()}
      </label>

      <div class="grid grid-cols-2 gap-3">
        {#each languages as lang}
          <button
            onclick={() => selectLang(lang.code)}
            class="p-4 rounded-lg border-2 transition-all
              {selectedLang === lang.code ? 'border-primary bg-primary/20' : 'border-border bg-muted hover:bg-accent'}"
          >
            <span class="text-lg">{lang.name}</span>
          </button>
        {/each}
      </div>
    </div>

    <div class="mb-8">
      <label class="text-lg mb-4 block">
        {t.Setup.SelectTheme()}
      </label>

      <div class="grid grid-cols-3 gap-3">
        {#each themes as theme}
          <button
            onclick={() => selectTheme(theme.code)}
            class="p-4 rounded-lg border-2 transition-all
              {selectedTheme === theme.code ? 'border-primary bg-primary/20' : 'border-border bg-muted hover:bg-accent'}"
          >
            <span class="text-lg">{theme.name}</span>
          </button>
        {/each}
      </div>
    </div>

    <button
      onclick={handleStart}
      class="w-full py-4 bg-primary hover:bg-primary/90 text-primary-foreground text-xl font-semibold rounded-lg transition-colors"
    >
      {t.Setup.Start()}
    </button>
  </div>
</div>

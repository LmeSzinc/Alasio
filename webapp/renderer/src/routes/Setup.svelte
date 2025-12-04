<script lang="ts">
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { setLanguage } from "$lib/i18n/index.svelte";
  import * as t from "$lib/i18n/setup";

  const sharedState = useSharedState();
  let selectedLang = $state(sharedState.language);

  const languages = [
    { code: "zh-CN", name: "简体中文" },
    { code: "en-US", name: "English" },
    { code: "ja-JP", name: "日本語" },
    { code: "zh-TW", name: "繁體中文" },
    { code: "es-ES", name: "Español" },
  ];

  async function handleStart() {
    setLanguage(selectedLang);
    await window.electronAPI.saveFirstTimeConfig(selectedLang);
  }
</script>

<div class="flex h-screen items-center justify-center bg-background text-foreground">
  <div class="w-[600px] rounded-xl bg-card border border-border p-12 shadow-lg">
    <h1 class="text-5xl font-bold mb-4">Alasio</h1>
    <p class="text-xl text-muted-foreground mb-12">{t.Welcome()}</p>

    <div class="mb-8">
      <label class="text-lg mb-4 block">
        {t.SelectLanguage()}
      </label>

      <div class="grid grid-cols-2 gap-3">
        {#each languages as lang}
          <button
            onclick={() => (selectedLang = lang.code)}
            class="p-4 rounded-lg border-2 transition-all
              {selectedLang === lang.code ? 'border-primary bg-primary/20' : 'border-border bg-muted hover:bg-accent'}"
          >
            <span class="text-lg">{lang.name}</span>
          </button>
        {/each}
      </div>
    </div>

    <button
      onclick={handleStart}
      class="w-full py-4 bg-primary hover:bg-primary/90 text-primary-foreground text-xl font-semibold rounded-lg transition-colors"
    >
      {t.Start()}
    </button>
  </div>
</div>

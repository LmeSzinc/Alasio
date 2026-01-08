<script lang="ts">
  import AppHeader from "$lib/components/header/AppHeader.svelte";
  import { t } from "$lib/i18n";

  let { children } = $props();
</script>

<div class="flex min-h-screen flex-col">
  <AppHeader>
    <h1 class="w-full text-center text-lg">{t.Auth.Login()}</h1>
  </AppHeader>
  <div class="dotbg flex-1">
    {@render children()}
  </div>
</div>

<script lang="ts">
  import AppHeader from "$lib/components/header/AppHeader.svelte";
  import { t } from "$lib/i18n";

  let { children } = $props();
</script>

<AppHeader>
  <h1 class="w-full text-center text-lg">{t.Auth.Login()}</h1>
</AppHeader>
{@render children()}

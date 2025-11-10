<script lang="ts">
  import AppHeader from "$lib/components/header/AppHeader.svelte";

  let { children } = $props();
</script>

<AppHeader>
  <h1 class="w-full text-center text-lg">Login</h1>
</AppHeader>
{@render children()}

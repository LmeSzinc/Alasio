<script>
  import ConfigAside from "$lib/components/aside/ConfigAside.svelte";
</script>

<div class="app-container">
  <!-- <Header /> -->
  <ConfigAside />
  <main class="app-content">
    <slot />
  </main>
</div>

<script lang="ts">
  // `/config` without a configuration name is a way to say "open whatever the
  // app opens": the same resolver as the app root handles it, instead of the
  // router falling through to a 404.
  import Entry from "../Entry.svelte";
</script>

<Entry />

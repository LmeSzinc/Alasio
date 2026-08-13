<script lang="ts">
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
</script>

<!-- Full-viewport iframe: the embedded web app provides its own header,
     the floating TitleBar overlay (drag strip + window controls) sits on top -->
<div class="flex h-screen flex-col">
  <iframe
    src="http://127.0.0.1:{sharedState.webuiPort}/?embedded=electron"
    class="flex-1 w-full border-0"
    title="Alasio App"
  />
</div>

<script lang="ts">
  import TitleBar from "$lib/components/TitleBar.svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
</script>

<div class="flex h-screen flex-col">
  <iframe src="http://127.0.0.1:{sharedState.webuiPort}" class="flex-1 w-full border-0" title="Alasio App" />
</div>

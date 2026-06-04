<script lang="ts">
  import { useTopic } from "$lib/ws";
  import LogDisplay from "./LogDisplay.svelte";
  import type { LogDataProps } from "./types";

  type Props = {
    class?: string;
  };
  let { class: className }: Props = $props();

  const logClient = useTopic<LogDataProps[]>("Log");
</script>

<LogDisplay data={logClient.data} class={className} />

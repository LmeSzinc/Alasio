<script module lang="ts">
  // How long the entry page waits for a configuration before it hands the user
  // to the config manager. The backend creates a default configuration per mod
  // in a background task of its first start, so the list legitimately arrives
  // late; a backend that will never have one (an install without mods, or every
  // configuration deleted) would otherwise keep the page waiting forever.
  export const FALLBACK_DELAY = 10_000;
</script>

<script lang="ts">
  import { goto } from "$app/navigation";
  import type { ConfigTopicLike } from "$lib/components/aside/types";
  import { HeaderContext } from "$lib/slotcontext.svelte";
  import { useTopic } from "$lib/ws";
  import { configOverviewPath, firstConfig } from "./entry";

  // Resolver of the app entry: it draws nothing and only decides which page the
  // user lands on. Both routes without a configuration render it — the app root
  // and `/config` without a name.
  //
  // Drawing nothing is the point: a spinner or a "loading" line would flash on
  // every start for the length of one redirect, while an empty page grows into
  // the config page and the shell (aside, header) is already painted by the
  // layout.
  //
  // The ConfigScan topic is the only source of the list. A fresh install fills
  // it from the backend's own background task, so waiting on the topic is all
  // this component has to do: the data may arrive after it mounted.
  const configClient = useTopic<ConfigTopicLike | undefined>("ConfigScan");

  // No configuration is open yet, so the header names the app instead of
  // letting the placeholder of the layout show through.
  HeaderContext.use(header);

  // One navigation for the lifetime of the component: the topic may push again
  // while the first navigation is still in flight, and a second goto would
  // cancel it. The fallback goes through the same guard, so a configuration
  // that appears later never overrides the page the user was handed to.
  let navigating = false;

  async function navigate(path: string) {
    if (navigating) return;
    navigating = true;
    try {
      // replaceState: a page without a configuration is not a place to come
      // back to, the browser's back button belongs to the visited pages
      await goto(path, { replaceState: true });
    } catch (error) {
      // A superseded navigation (the user clicked a configuration of the
      // sidebar first) leaves this page anyway; a failed one stays here, so a
      // later update of the list retries.
      console.debug("Entry navigation did not complete:", error);
      navigating = false;
    }
  }

  $effect(() => {
    const config = firstConfig(configClient.data);
    if (!config) return;
    void navigate(configOverviewPath(config.name));
  });

  $effect(() => {
    const timer = setTimeout(() => void navigate("/dev/config"), FALLBACK_DELAY);
    return () => clearTimeout(timer);
  });
</script>

{#snippet header()}
  <h1 class="w-full flex-1 text-center text-lg">Alasio</h1>
{/snippet}

<!-- Intentionally empty: the layout's shell is painted, the redirect replaces
     this component before it could ever hold content -->
<div class="h-full"></div>

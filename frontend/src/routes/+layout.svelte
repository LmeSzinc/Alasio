<script lang="ts">
  import { Toaster } from "$lib/components/ui/sonner";
  import { ModeWatcher } from "mode-watcher";
  import "../app.css";

  let { children } = $props();
</script>

<ModeWatcher />
<Toaster richColors closeButton duration={5000} position="top-right" expand={true} />
{@render children()}

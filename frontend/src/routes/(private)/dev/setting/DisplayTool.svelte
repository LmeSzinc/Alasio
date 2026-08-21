<script lang="ts">
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { Checkbox } from "$lib/components/ui/checkbox";
  import ThemeToggle from "$lib/components/ui/theme/theme-toggle.svelte";
  import LangSelector from "$lib/i18n/LangSelector.svelte";
  import { t } from "$lib/i18n";
  import { isElectron } from "$lib/use/useElectronEnv.svelte";

  // dpiScaling is display-only for now: the real scaling is applied by the
  // host (electron main process), so the checkbox keeps a local value and is
  // disabled outside an embedded (electron) session.
  let dpiScaling = $state(false);

  // $derived so name/help follow the current display language
  const langData = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "DisplayLang",
    dt: "static",
    value: null,
    name: t.DevTool.Language(),
  }));

  const themeData = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "DisplayTheme",
    dt: "static",
    value: null,
    name: t.DevTool.Theme(),
  }));

  const dpiData = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "DpiScaling",
    dt: "static",
    value: dpiScaling,
    name: t.DevTool.DpiScaling(),
    help: t.DevTool.DpiScalingHelp(),
  }));
</script>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike data={langData}>
    {#snippet InputSnippet()}
      <LangSelector />
    {/snippet}
  </LayoutHorizontalLike>
</div>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike data={themeData}>
    {#snippet InputSnippet()}
      <ThemeToggle />
    {/snippet}
  </LayoutHorizontalLike>
</div>

<hr />
<div class="flex flex-col gap-y-1.5">
  <LayoutHorizontalLike data={dpiData}>
    {#snippet InputSnippet()}
      <Checkbox bind:checked={dpiScaling} disabled={!isElectron.value} class="size-4.5" iconStrokeWidth={3.5} />
    {/snippet}
  </LayoutHorizontalLike>
</div>

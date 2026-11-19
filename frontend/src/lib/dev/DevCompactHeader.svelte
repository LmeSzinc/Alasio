<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { MOD_SELECTOR_CLASS } from "$lib/window/regions";

  // `mod_name` is bindable so the selector keeps working when the list changes
  // under it (a mod is added or removed while the page is open).
  let {
    mod_name = $bindable(),
    handleEdit,
    title,
  }: {
    mod_name: string;
    handleEdit: (value: string) => void;
    title: string;
  } = $props();
</script>

<!--
  The header of a compact dev page: the page title centred over the page area,
  the mod selector at its right end, next to the language and theme buttons the
  app header draws.

  Centring is `text-center` on a `flex-1` box, the same rule every other page
  header uses (`ConfigNav`, the dev layout): the title centres in the space the
  header gives the page, not in the window, because the language, theme and
  window buttons take the right end of the bar. Doing it any other way here
  would make this title sit at a different place than the title of every user
  tool page next to it.

  A narrow window gives the title and the selector whatever is left, so both
  shrink (`min-w-0`, and the selector keeps its half-of-the-row cap) and are cut
  off at the edge without an ellipsis (`overflow-hidden` rather than `truncate`).
-->
<div class="flex min-w-0 flex-1 items-center gap-2">
  <h1 class="min-w-0 flex-1 overflow-hidden text-center text-lg whitespace-nowrap">{title}</h1>
  <ModSelector class={MOD_SELECTOR_CLASS} {mod_name} {handleEdit} />
</div>

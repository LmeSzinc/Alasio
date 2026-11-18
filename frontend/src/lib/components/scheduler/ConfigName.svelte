<script lang="ts">
  import { cn } from "$lib/utils";
  import FitText from "./FitText.svelte";

  /**
   * The config name of the scheduler, in the largest of three sizes that fits.
   *
   * The name is a user string of any length, so the row cannot give it a fixed
   * size: it takes the width the row leaves, and the text steps down from
   * `text-lg` to `text-base` to `text-sm`. Only when even `text-sm` does not fit
   * on one line does the name wrap, and then in two lines with `wrap-break-word`
   * (a name may be one long token, e.g. a config id).
   */
  type Props = {
    text: string;
    class?: string;
  };
  let { text = "", class: className = "" }: Props = $props();

  const SIZES = [
    { probe: "text-lg font-semibold", display: "truncate text-lg font-semibold" },
    { probe: "text-base font-semibold", display: "truncate text-base font-semibold" },
    { probe: "text-sm font-semibold", display: "text-sm font-semibold" },
  ];
</script>

<FitText {text} sizes={SIZES} class={cn("min-w-0", className)} />

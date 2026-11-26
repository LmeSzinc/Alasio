<script lang="ts">
  import type { Component } from "svelte";
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";

  type IconComponent = Component<{ class?: string }>;

  type Props = {
    /** Icon drawn left of the label, a lucide icon component */
    icon: IconComponent;
    /** Label text, already translated by the caller */
    label: string;
    /**
     * True while the button keeps something open. Such a button stays visible
     * and keeps its rule drawn without a hover, and its label turns primary:
     * whatever it opened has to be closable from here.
     */
    active?: boolean;
    /**
     * True for a toggle, whose state is exposed as `aria-pressed`. A plain
     * action button (the reset of a card) leaves it false.
     */
    toggle?: boolean;
    onclick: () => void;
    class?: string;
  };
  let { icon: Icon, label, active = false, toggle = false, onclick, class: className }: Props = $props();

  // The rule under the label is a pseudo element of the content wrapper, not
  // `text-decoration`: the button is a flex container and a text decoration
  // does not reach the icon, so the line would start under the label and leave
  // the icon floating. The wrapper spans both, so the rule runs under the icon.
  const contentClass =
    "relative inline-flex items-center gap-x-1 " +
    "after:absolute after:inset-x-0 after:-bottom-0.5 after:h-px after:bg-current after:transition-opacity after:content-['']";
</script>

<!-- A hover action of the card header. Hidden until the pointer is on the card,
     so the header stays quiet, and filled in while `active` holds.

     Horizontal padding is narrower than a plain button of this size: two of
     these sit side by side with nothing between them, so their paddings add up
     into the gap the user reads between the two labels. The row that places
     them gives the missing width back on its other side, see CardEnable. -->
<Button
  variant="ghost"
  class={cn(
    "gap-x-1 px-1.5 transition-opacity duration-200 hover:bg-transparent dark:hover:bg-transparent",
    active
      ? "pointer-events-auto text-primary opacity-100 hover:text-primary/80"
      : "pointer-events-none text-muted-foreground opacity-0 group-hover/card:pointer-events-auto group-hover/card:opacity-100",
    className,
  )}
  aria-pressed={toggle ? active : undefined}
  {onclick}
>
  <span class={cn(contentClass, active ? "after:opacity-100" : "after:opacity-0 group-hover/button:after:opacity-100")}>
    <!-- The icon drops 1px: the row centers the icon box on the line box, while
         the ink of the 14px label sits about a pixel below that box centre, so
         an icon centred on the box reads as too high next to the label. -->
    <Icon class="size-3.5 translate-y-px" />
    <p>{label}</p>
  </span>
</Button>

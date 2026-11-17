<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "$lib/utils";

  /**
   * A ringing sway: the content swings left and right around a pivot at its
   * top edge, like a bell hanging from its handle.
   *
   * One cycle is a full left -> right -> left swing, so the keyframes start and
   * end on the same angle: the loop has no seam and needs no timer.
   *
   * The component always sways: a state that should not sway simply does not
   * wrap its icon in it, which keeps the call site and the markup it renders in
   * sight of each other.
   *
   * The wrapper's box is exactly the content's box, so wrapping an icon does not
   * move it inside the page, and the pivot is that box's top edge instead of a
   * line box around it.
   */
  type Props = {
    /** The content to sway. */
    children: Snippet;
    /** Class of the sway wrapper: the box of the content, e.g. its size. */
    class?: string;
  };
  let { children, class: className }: Props = $props();
</script>

<span class={cn("anim-shake", className)}>
  {@render children()}
</span>

<style>
  /* Load-bearing geometry, not decoration:

     - `display: block` keeps the wrapper out of a line box. Tailwind Preflight
       renders svg as `display: block`, so without a wrapper the parent box is
       exactly the icon. An inline-level wrapper would create a line box around
       it instead: the parent grows to the line-height and keeps the descent
       below the baseline, so the icon climbs by that descent (7.2px at the
       body line-height, 3.2px at text-sm) -- a badge anchored to a corner
       visibly moves up.
     - `line-height: 0` covers the other case: an inline content (no Preflight,
       or a non-svg glyph) gets no leading of its own.
     - the pivot is the top edge, where a bell hangs from.

     Own rules instead of utility classes, so the geometry cannot break on a
     Tailwind content-scan miss. */
  .anim-shake {
    display: block;
    line-height: 0;
    transform-origin: 50% 0%;
    animation: anim-shake 0.6s ease-in-out infinite;
  }

  /* A pendulum, not a jitter: the content travels to the extreme on each side
     and slows down there, which is what makes it read as a bell being rung. */
  @keyframes anim-shake {
    0%,
    100% {
      transform: rotate(-10deg);
    }
    50% {
      transform: rotate(10deg);
    }
  }

  /* An endless motion is what motion-sensitive users ask the system to turn
     off; the icon then simply stands still. */
  @media (prefers-reduced-motion: reduce) {
    .anim-shake {
      animation: none;
    }
  }
</style>

<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "$lib/utils";

  /**
   * A looping rise: one glyph travels up out of a window while a copy of it
   * enters from the bottom, so the motion never stops or jumps.
   *
   * Pure CSS, no timer. The content is rendered twice into a two-copy column
   * and the column travels up by half of its own height per cycle, i.e.
   * exactly one copy: when a cycle ends the second copy stands where the first
   * one started, and the restart of the animation is invisible.
   *
   * The window must be exactly as tall as one copy, so the same size class is
   * given to the wrapper and to the content, e.g. `class="h-3 w-3"` around an
   * icon at `h-3 w-3`.
   */
  type Props = {
    /** The content to rise: rendered twice (the second copy is aria-hidden). */
    children: Snippet;
    /** Window class: the box of exactly one copy, including its size. */
    class?: string;
    /** Time one copy needs to travel through the window. */
    duration?: string;
    /**
     * Width of the fade at each edge of the window.
     *
     * Keep it inside the padding of the glyph: a fade wider than the padding
     * reaches into the glyph itself and dims its tip and tail at every phase,
     * so the motion reads as a pulsing smudge instead of a moving glyph. Icons
     * with a tight box (lucide at `h-3` has 2px of padding) need 1px or so.
     */
    fade?: string;
  };
  let { children, class: className, duration = "0.8s", fade = "1px" }: Props = $props();
</script>

<!-- The window clips; the fade sits on the window and not on the moving column:
     a mask is applied in the local space of its own element, so one on the
     column would travel with it and the edges would never fade. -->
<span
  class={cn("anim-up-window relative block overflow-hidden", className)}
  style="--anim-up-fade: {fade}; --anim-up-duration: {duration}"
>
  <span class="anim-up flex flex-col">
    {@render children()}
    <!-- `contents` keeps the wrapper out of the column layout: the copies stay
         siblings, and the duplicate is invisible to assistive technology -->
    <span class="anim-up-copy" aria-hidden="true">{@render children()}</span>
  </span>
</span>

<style>
  .anim-up-window {
    mask-image: linear-gradient(
      to bottom,
      transparent 0,
      black var(--anim-up-fade),
      black calc(100% - var(--anim-up-fade)),
      transparent 100%
    );
    -webkit-mask-image: linear-gradient(
      to bottom,
      transparent 0,
      black var(--anim-up-fade),
      black calc(100% - var(--anim-up-fade)),
      transparent 100%
    );
  }

  /* Load-bearing geometry, not decoration: the wrapper of the second copy must
     not become a flex item, or the column would be three boxes tall and the
     50% travel would no longer equal one copy. Own rule instead of a utility
     class, so the animation cannot break on a Tailwind content-scan miss. */
  .anim-up-copy {
    display: contents;
  }

  .anim-up {
    animation: anim-up var(--anim-up-duration) linear infinite;
  }

  @keyframes anim-up {
    from {
      transform: translateY(0);
    }
    to {
      /* half of the two-copy column = exactly one copy */
      transform: translateY(-50%);
    }
  }

  /* An endless motion is what motion-sensitive users ask the system to turn
     off; the glyph then simply sits in the window. */
  @media (prefers-reduced-motion: reduce) {
    .anim-up {
      animation: none;
    }
  }
</style>

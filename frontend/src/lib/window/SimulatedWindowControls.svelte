<script lang="ts">
  import Minimize2 from "@lucide/svelte/icons/minimize-2";
  import Minus from "@lucide/svelte/icons/minus";
  import Square from "@lucide/svelte/icons/square";
  import X from "@lucide/svelte/icons/x";
  import { Button } from "$lib/components/ui/button";

  // Stand-ins for the electron window controls, drawn only when their width is
  // simulated (see `frame.controls.source`): in `vite dev` there is no host, so
  // without them the reserved space on the right of the header looks empty and
  // the layout cannot be judged.
  //
  // The geometry and the hover styling mirror the host's group
  // (`webapp/renderer/lib/components/WindowControls.svelte`): four `size-9`
  // ghost buttons, `gap-1` between them and `pr-1.5` inside the group, so the
  // buttons end 6px from the window edge and the whole reserve is
  // 4 * 36 + 3 * 4 + 6 + 6 = 168. Keep the two in step.
  //
  // They look live but do nothing: no handlers, `tabindex={-1}` and
  // `aria-hidden` keep them out of the keyboard order and the accessibility
  // tree, so a button that does nothing is never announced or focused as one.
</script>

<div
  class="absolute top-0 right-0 flex h-12 items-center gap-1 pr-1.5 select-none"
  aria-hidden="true"
  data-slot="simulated-window-controls"
>
  <Button variant="ghost" size="icon" class="dark:hover:bg-muted" tabindex={-1}>
    <Minimize2 class="h-4 w-4" strokeWidth={1.5} />
  </Button>
  <Button variant="ghost" size="icon" class="dark:hover:bg-muted" tabindex={-1}>
    <Minus class="h-4 w-4" strokeWidth={1.5} />
  </Button>
  <Button variant="ghost" size="icon" class="dark:hover:bg-muted" tabindex={-1}>
    <Square class="h-4 w-4" strokeWidth={1.5} />
  </Button>
  <Button
    variant="ghost"
    size="icon"
    class="hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive dark:hover:text-destructive-foreground"
    tabindex={-1}
  >
    <X class="h-4 w-4" strokeWidth={1.5} />
  </Button>
</div>

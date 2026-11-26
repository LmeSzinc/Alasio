<script lang="ts">
  import Cog from "@lucide/svelte/icons/cog";
  import { Badge } from "$lib/components/ui/badge";
  import { t } from "$lib/i18n";
</script>

<!-- Marks a row that only the advanced settings of the card reveal, so the
     user can tell it apart from the rows they always see. The cog is the icon
     of the toggle that brought the row on screen (the badge sizes it to 12px),
     and the muted text keeps the label from reading as part of the arg name.

     The small shift down corrects an optical offset, not a layout one: the row
     centers its items on the line box of the arg name, while the ink of a 14px
     text sits slightly below that line box center, so a pill that is
     geometrically centered next to it reads as too high.

     The left padding is smaller than the right one on purpose: the 12px cog is
     centered in the 16px pill (2px of space above and below it), and 2px on the
     left puts its centre on the centre of the rounded cap, so the cap reads as
     a ring drawn around the icon instead of a ring with a stray gap. The border
     of the badge base is transparent here, so it is dropped: it would otherwise
     take its width out of the padded inset and push the icon off that centre. -->
<Badge
  variant="secondary"
  class="h-4 translate-y-0.5 gap-0.5 border-0 pr-1.5 pl-0.5 text-xs leading-none text-muted-foreground"
>
  <Cog />
  {t.Input.Advanced()}
</Badge>

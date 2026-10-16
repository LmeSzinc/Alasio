<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { prettyVersion } from "./utils";

  /**
   * Version of a mod as a pill: the sha1 of a packed commit shortened to 8
   * characters, a tag as it is, "Local version unavailable" when the pack of
   * the mod reports no version. The tooltip carries the raw version.
   *
   * Abstracted from the title row of ModInfo and shared with the update toast:
   * both show the version of a mod the same way. The width is the caller's
   * business (the toast gives it half of its row and truncates it), the badge
   * only knows the version.
   */
  type $props = {
    /** Version of the mod, raw: a packed sha1 is shortened for display */
    version: string;
    class?: string;
  };
  let { version, class: className }: $props = $props();
</script>

<Badge variant="default" class={cn("bg-blue-600 font-mono", className)} title={version}>
  <!-- the text sits in an element of its own: a caller may constrain the
       badge to a part of its row, and the truncation needs a box inside the
       inline-flex to act on -->
  <span class="min-w-0 truncate">{version ? prettyVersion(version) : t.Update.VersionUnavailable()}</span>
</Badge>

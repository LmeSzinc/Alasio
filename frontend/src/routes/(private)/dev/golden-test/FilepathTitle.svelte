<script lang="ts">
  /**
   * The path of a reviewed file, as the review page shows it.
   *
   * The same path appears in two places -- the toolbar of the workspace and the
   * overlay that shows a screenshot at full size -- and both have to read the
   * same, so the look lives here rather than in the two rows that render it:
   * monospace, at the size of the body text, in the foreground color, truncated
   * from the middle of the path instead of wrapping, and inset a little from the
   * row it starts.
   *
   * The component takes its place from the parent: it is the flexible cell of a
   * row (`min-w-0`, `flex-1`), so the controls at the end of the row keep their
   * width at any window size.
   */
  type Props = {
    /** The path to show, empty for a view that has no file yet */
    path: string;
    /** Shown while there is no path, a dash by default */
    placeholder?: string;
  };

  let { path, placeholder = "—" }: Props = $props();
</script>

<span class="min-w-0 flex-1 truncate pl-2 font-mono text-sm" title={path}>
  {path || placeholder}
</span>

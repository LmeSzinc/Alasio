<script lang="ts">
  import MessageCircleQuestionMark from "@lucide/svelte/icons/message-circle-question-mark";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { t } from "$lib/i18n";
  import { pinToAnchor } from "$lib/use/scroll.svelte";
  import { cn } from "$lib/utils";

  let {
    helpVisible = $bindable(),
    class: className,
  }: {
    helpVisible: boolean;
    class?: string;
  } = $props();
</script>

<Tooltip.Provider>
  <Tooltip.Root>
    <Tooltip.Trigger>
      <button
        class={cn(
          "inline-flex shrink-0 cursor-pointer items-center justify-center p-1 text-muted-foreground/50 transition-colors hover:text-primary",
          helpVisible ? "text-primary" : "",
          className,
        )}
        onclick={(event) => pinToAnchor(event.currentTarget, () => (helpVisible = !helpVisible))}
        aria-label={t.Input.ShowHelp()}
      >
        <MessageCircleQuestionMark size={16} />
      </button>
    </Tooltip.Trigger>
    <Tooltip.Content>
      <p>{t.Input.ShowHelp()}</p>
    </Tooltip.Content>
  </Tooltip.Root>
</Tooltip.Provider>

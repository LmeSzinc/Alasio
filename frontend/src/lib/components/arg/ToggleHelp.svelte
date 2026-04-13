<script lang="ts">
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { MessageCircleQuestionMark } from "@lucide/svelte";

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
          "text-muted-foreground/50 hover:text-primary inline-flex shrink-0 cursor-pointer items-center justify-center p-1 transition-colors",
          helpVisible ? "text-primary" : "",
          className,
        )}
        onclick={() => (helpVisible = !helpVisible)}
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

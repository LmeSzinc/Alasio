<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import I18nText from "./I18nText.svelte";

  type Props = {
    /**
     * Card title shown in the header, e.g. `t.DevTool.SystemTool()`.
     * The caller is responsible for resolving i18n text.
     */
    title: string;
    /**
     * Optional help text shown below the title as a card description.
     * Supports i18n text or a list of lines.
     */
    help?: string | string[];
    /**
     * Optional extra content rendered in the header below title/help,
     * e.g. scheduler args or enable controls for the settings page.
     */
    headerExtra?: Snippet;
    /**
     * Card content, e.g. arg rows separated by `<hr />`.
     */
    children: Snippet;
    flashing?: boolean;
    class?: string;
  };

  let { title, help, headerExtra, children, flashing = false, class: className }: Props = $props();
</script>

<Card.Root
  class={cn("group/card neushadow relative mx-auto gap-0 border-none", flashing && "animate-flash-primary", className)}
>
  <!-- Group name and help -->
  <Card.Header class="flex flex-col gap-y-1.5">
    <!-- Group name -->
    <div class="flex w-full items-center justify-between gap-x-4">
      <Card.Title class="flex-1 text-2xl font-bold">{title}</Card.Title>
    </div>
    {#if help}
      <Card.Description>
        <I18nText text={help} />
      </Card.Description>
    {/if}
    {#if headerExtra}
      {@render headerExtra()}
    {/if}
  </Card.Header>
  <!-- Group content -->
  <Card.Content class="flex flex-col gap-y-2 pt-2">
    {@render children()}
  </Card.Content>
</Card.Root>

<style>
  @keyframes flash-primary {
    0%,
    40%,
    80%,
    100% {
      outline-color: transparent;
    }
    20%,
    60% {
      outline-color: var(--primary);
    }
  }

  :global(.animate-flash-primary) {
    outline: 2px solid transparent;
    outline-offset: -2px;
    animation: flash-primary 0.8s ease-in-out;
    /* Ensure it doesn't take space */
    position: relative;
    z-index: 10;
  }
</style>

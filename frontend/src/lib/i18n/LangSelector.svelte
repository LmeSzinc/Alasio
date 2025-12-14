<script lang="ts">
  import Button from "$lib/components/ui/button/button.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import { i18nState, setLang, t } from "$lib/i18n";
  import type { Lang } from "$lib/i18n/state.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { CircleCheck, Languages } from "@lucide/svelte";
  import { SUPPORTED_LANGS } from "../../i18ngen/constants";

  type Props = {
    disabled?: boolean;
    class?: string;
    // Optional callback when value changed
    handleEdit?: (value: Lang) => void;
  };
  let { disabled = false, class: className, handleEdit }: Props = $props();

  const topicClient = useTopic("ConnState");
  const rpc = topicClient.resilientRpc();
  const languageNames: Record<string, string> = {
    "en-US": "English",
    "zh-CN": "简体中文",
    "ja-JP": "日本語",
    "zh-TW": "繁體中文",
    "es-ES": "Español",
  };

  let open = $state(false);
  $effect(() => {
    rpc.call("set_lang", { lang: i18nState.l });
  });
  function selectLanguage(lang: Lang) {
    if (lang === i18nState.l) return;
    setLang(lang);
    open = false;
    handleEdit?.(lang);
  }
</script>

<Popover.Root bind:open>
  <Popover.Trigger
    class={cn(
      "focus-visible:ring-ring ring-offset-background hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
      "inline-flex h-9 w-9 items-center justify-center rounded-md text-sm font-medium transition-colors",
      "disabled:pointer-events-none disabled:opacity-50",
      className,
    )}
    aria-label={t.Language.SelectLanguage()}
    {disabled}
  >
    <Languages class="h-4 w-4" strokeWidth={1.5} />
  </Popover.Trigger>

  <Popover.Content class="w-48 p-1" align="end">
    <div class="space-y-1">
      {#each SUPPORTED_LANGS as lang}
        {@const variant = i18nState.l === lang ? "default" : "ghost"}
        <Button class="w-full" {variant} onclick={() => selectLanguage(lang)}>
          {#if i18nState.l === lang}
            <CircleCheck class="text-primary-foreground" />
          {/if}
          <span class="flex-1 text-left">
            {languageNames[lang]}
          </span>
        </Button>
      {/each}
    </div>
  </Popover.Content>
</Popover.Root>

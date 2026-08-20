<script lang="ts">
    import SunIcon from "@lucide/svelte/icons/sun";
    import MoonIcon from "@lucide/svelte/icons/moon";
    import MonitorIcon from "@lucide/svelte/icons/monitor";
    import { Check } from "@lucide/svelte";

    import { userPrefersMode } from "mode-watcher";
    import Button from "$lib/components/ui/button/button.svelte";
    import * as Popover from "$lib/components/ui/popover";
    import { t } from "$lib/i18n";
    import { reportTheme, type ConfigTheme } from "$lib/theme/state.svelte";
    import { cn } from "$lib/utils";
    import type { ButtonProps } from "$lib/components/ui/button/index.js";
    let {
		class: className,
	}: ButtonProps = $props();

    // Three-option selector (system / light / dark) in a popover, same
    // pattern as LangSelector. Reads and writes the user preference
    // (mode-watcher persists it to localStorage). In an embedded session
    // the selection is reported to the host as the config value, and the
    // host sends back the concrete display theme.
    const themeOptions = $derived([
        { value: "system" as const, icon: MonitorIcon, name: t.Theme.System() },
        { value: "light" as const, icon: SunIcon, name: t.Theme.Light() },
        { value: "dark" as const, icon: MoonIcon, name: t.Theme.Dark() },
    ]);

    let open = $state(false);

    function selectTheme(theme: ConfigTheme) {
        if (theme === userPrefersMode.current) return;
        userPrefersMode.current = theme;
        reportTheme(theme);
        open = false;
    }
</script>

<Popover.Root bind:open>
    <Popover.Trigger
        class={cn(
            "focus-visible:ring-ring ring-offset-background hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
            "inline-flex h-9 w-9 items-center justify-center rounded-md text-sm font-medium transition-colors",
            "disabled:pointer-events-none disabled:opacity-50",
            className,
        )}
        aria-label={t.Theme.SelectTheme()}
    >
        <SunIcon class="h-4 w-4" strokeWidth={1.5} />
    </Popover.Trigger>

    <Popover.Content class="w-48 p-1" align="end">
        <div class="space-y-1">
            {#each themeOptions as opt}
                {@const Icon = opt.icon}
                <Button
                    class="w-full justify-between font-normal"
                    variant={userPrefersMode.current === opt.value ? "default" : "ghost"}
                    onclick={() => selectTheme(opt.value)}
                >
                    <span class="flex items-center gap-2">
                        <Icon class="h-4 w-4" strokeWidth={1.5} />
                        {opt.name}
                    </span>
                    {#if userPrefersMode.current === opt.value}
                        <Check class="h-4 w-4" />
                    {/if}
                </Button>
            {/each}
        </div>
    </Popover.Content>
</Popover.Root>

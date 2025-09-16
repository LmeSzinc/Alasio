<script lang="ts">
    import SunIcon from "@lucide/svelte/icons/sun";
    import MoonIcon from "@lucide/svelte/icons/moon";
    
    import { toggleMode } from "mode-watcher";
    import { Button, type ButtonProps } from "$lib/components/ui/button/index.js";
    let {
		class: className,
		variant = "outline",
		size = "icon",
		ref = $bindable(null),
	}: ButtonProps = $props();
</script>

<Button bind:ref class={className} onclick={toggleMode} {variant} {size}>
    <SunIcon
        class="h-[1.2rem] w-[1.2rem] rotate-0 scale-100 !transition-all dark:-rotate-90 dark:scale-0"
    />
    <MoonIcon
        class="absolute h-[1.2rem] w-[1.2rem] rotate-90 scale-0 !transition-all dark:rotate-0 dark:scale-100"
    />
    <span class="sr-only">Toggle theme</span>
</Button>

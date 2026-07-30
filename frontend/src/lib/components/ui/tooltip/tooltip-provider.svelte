<script lang="ts">
	import { Tooltip as TooltipPrimitive } from "bits-ui";

	// MODIFIED: remove transition-all for better switching between themes
	let { delayDuration = 700, ...restProps }: TooltipPrimitive.ProviderProps = $props();
</script>

<TooltipPrimitive.Provider {delayDuration} {...restProps} />

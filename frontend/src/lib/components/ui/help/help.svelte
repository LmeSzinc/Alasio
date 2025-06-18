<script lang="ts" module>
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn, type WithElementRef } from '$lib/utils.js';
	import { type VariantProps, tv } from 'tailwind-variants';

	export const helpVariants = tv({
		base: 'flex flex-col gap-2 px-2 py-2 border-l-6 text-xs',
		variants: {
			variant: {
				default: 'bg-muted text-muted-foreground',
				error: 'border-l-destructive bg-destructive/8 text-destructive'
			}
		},
		defaultVariants: {
			variant: 'default'
		}
	});

	export type HelpVariant = VariantProps<typeof helpVariants>['variant'];
	export type HelpProps = WithElementRef<HTMLAttributes<HTMLDivElement>> & {
		variant?: HelpVariant;
	};
</script>

<script lang="ts">
	let {
		class: className,
		variant = 'default',
		ref = $bindable(null),
		children,
		...restProps
	}: HelpProps = $props();
</script>

<div
  bind:this={ref}
  data-slot="help"
  class={cn(helpVariants({ variant }), className)}
  {...restProps}
>
  {@render children?.()}
</div>

/**
 * Width of the card column shared by the config pages and the dev pages: the
 * cards of a page (the arg cards of a config, the mod cards, the settings
 * cards, ...) all follow the same rule, so they line up when the user moves
 * from one page to another.
 *
 * The rule, from the narrowest screen to the widest:
 * 1. follow the parent width until max-w-180
 * 2. keep max-w-180, let the margin auto increase, until card/parent <= 3/5
 * 3. keep the width ratio card/parent <= 3/5 until max-w-240
 * 4. keep max-w-240, let the margin auto increase
 *
 * Usage: the size of the container of the cards is measured with
 * `use:elementSize`, the class goes on every card of the container (the cards
 * keep `mx-auto`, so they stay centered):
 *
 * ```svelte
 * let containerSize = $state({ width: 0, height: 0 });
 * const cardClass = $derived(cardWidth(containerSize.width));
 * <div class="container ..." use:elementSize={containerSize}>
 *   <Card.Root class={cn("mx-auto", cardClass)}>...</Card.Root>
 * </div>
 * ```
 *
 * Args:
 *   parentWidth (number): Width of the container the cards are rendered in
 *
 * Returns:
 *   (str): Tailwind classes of the width of a card
 */
export function cardWidth(parentWidth: number): string {
  return parentWidth < 1200 ? "max-w-180" : parentWidth < 1600 ? "w-3/5" : "max-w-240";
}

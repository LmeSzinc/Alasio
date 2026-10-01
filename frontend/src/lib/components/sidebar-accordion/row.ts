/**
 * Metrics of a row of the sidebar accordion family.
 *
 * A nav sidebar stacks many rows in a narrow column, so the family trades the
 * generous padding of the base accordion (py-4 on a trigger, pb-4 on a
 * content) for the compact metrics of a sidebar row. Every row of the family
 * uses them, whatever kind it is, so the entries of a nav line up in one list.
 *
 * A row is laid out from left to right as: the padding of the metrics, the
 * select indicator (SidebarRowIndicator, every row reserves its space), the
 * label. The metrics own that whole box, so changing a value here changes
 * every row of the family and no call site repeats the padding of a row.
 *
 * The box of a row is what a hover paints, so it is held a little off the
 * edges of the list (mx-1) and rounded: the highlight of a row keeps a margin
 * from the edges of the nav or of the group content and does not hug the
 * label. The padding of the metrics is then what places the parts of a row:
 * pl-1 holds the select indicator 8px off the left edge of the nav, pr-2 holds
 * the chevron of a trigger (and the dot of a card row, which aligns to it) 12px
 * off the right edge, which puts the label 20px off the left edge.
 *
 * gap-0 cancels the flex gap a base button brings along: the space between
 * the indicator and the label belongs to the indicator (its mr), so a row
 * based on a button and a row based on the accordion trigger line up.
 *
 * border-0 drops the transparent 1px border a base button and a base accordion
 * trigger carry along (they reserve it for the variants that draw one). A row
 * of a nav never draws a border, and a border takes part in the layout: 1px at
 * 125% display scale is one device pixel, i.e. 0.8 CSS px, which would push
 * every padding of a row inward by that much and place the indicator 8.8px off
 * the left edge instead of 8px.
 */
export const sidebarRowClass = "mx-1 gap-0 rounded-md border-0 py-1.5 pr-2 pl-1 text-sm";

/**
 * The select indicator a row shows before its label: a bar painted when the
 * row is the current one. Its width and the gap before the label are part of
 * the layout of a row, so a row that can never be current (the trigger of a
 * group) reserves the same space instead of letting its label drift left.
 */
export const sidebarRowIndicatorClass = "mr-2 w-1 shrink-0 self-stretch rounded-full";

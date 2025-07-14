// 1. Import the CSS. This line ensures that whenever this module is imported,
//    the CSS is included in the build for the page that uses it.
import "./placeholder.css";

// 2. Re-export the Svelte components to make them available to consumers.
export { default as Indicator } from "./Indicator.svelte";
export { default as DndProvider } from "./dndProvider.svelte";

// 3. (Optional but good practice) Re-export any types that consumers might need.
export type { IndicatorEdge } from "./Indicator.svelte";
export type { DndEndCallbackDetail, DropIndicatorState } from "./dndProvider.svelte";

// 4. Ultility functions
export { applyDnd } from "./apply";

<script lang="ts">
  import {
    DndContext,
    DragOverlay,
    KeyboardSensor,
    MouseSensor,
    TouchSensor,
    useSensor,
    useSensors,
    type Active,
    type DragEndEvent,
    type DragOverEvent,
    type DragStartEvent,
    type DropAnimation,
    type Over,
  } from "@dnd-kit-svelte/core";
  import type { Snippet } from "svelte";
  import { createClosestEdgeCollision, verticalDistance, horizontalDistance } from "./collision";

  // --- Type Definitions ---
  export type DropIndicatorState = {
    targetId: string | number | null;
    position: "top" | "bottom" | "left" | "right";
    orientation: "horizontal" | "vertical";
  };

  export type DndEndCallbackDetail = {
    active: Active;
    over: Over;
    position: DropIndicatorState["position"];
  };

  /**
   * A map defining which active types can be dropped onto which droppable types.
   * Key: The `type` of the droppable element (`over.type`).
   * Value: An array of `type`s of the active draggable element (`active.type`) that are accepted.
   * If not provided, no type checking will occur.
   * @example { group: ['item', 'group'], item: ['item'] }
   */
  export type DndRules = Record<string, string[]>;

  type Props = {
    children: Snippet<[{ dropIndicator: DropIndicatorState }]>;
    dragOverlay: Snippet<[{ active: Active | null }]>;
    onDndEnd: (detail: DndEndCallbackDetail) => void;
    orientation?: "horizontal" | "vertical";
    // The dndRules prop is optional for maximum flexibility.
    dndRules?: DndRules;
  };

  // --- Component Props ---
  let { children, dragOverlay, onDndEnd, orientation = "vertical", dndRules = undefined }: Props = $props();

  // --- Svelte 5 State (Runes) ---
  let active = $state<Active | null>(null);
  let lastOver = $state<Over | null>(null);
  let dropIndicator = $state<DropIndicatorState>({
    targetId: null,
    position: "top", // Default position
    orientation,
  });
  // Reset indicator state at the start of a drag.
  function clearIndicator() {
    lastOver = null;
    dropIndicator = { targetId: null, position: "top", orientation };
  }

  // --- Collision Algorithm Creation ---
  // The collision algorithm is created reactively and internally based on props.
  // This is the "magic" that makes the provider so easy to use.
  const collisionDetection = $derived(
    createClosestEdgeCollision({
      orientation,
      accepts: dndRules,
    }),
  );

  // --- Drag Handlers ---

  // Define the input sensors. This is what enables dragging.
  const sensors = useSensors(useSensor(MouseSensor), useSensor(TouchSensor), useSensor(KeyboardSensor));

  // Define a null drop animation to prevent the "fly back" effect.
  const dropAnimation: DropAnimation | null = null;

  function handleDragStart(event: DragStartEvent) {
    active = event.active;
    clearIndicator();
  }

  function handleDragMove({ over, active }: DragOverEvent) {
    // We need the `active` object to get the pointer coordinates.
    // The pointer coordinates are not directly on the event, but we can get them
    // from the active element's translated rectangle.
    const activeRect = active.rect.translated;
    if (!activeRect) {
      clearIndicator();
      return;
    }
    const pointer = {
      x: activeRect.left + activeRect.width / 2,
      y: activeRect.top + activeRect.height / 2,
    };

    if (!over || !over.rect) {
      clearIndicator();
      return;
    }

    // The `over` object is the one our collision algorithm correctly chose.
    // Now, we perform the final geometric calculation here.
    // The `edge` is the calculated 'top', 'bottom', 'left', or 'right'.
    const { edge } =
      orientation === "vertical" ? verticalDistance(over.rect, pointer) : horizontalDistance(over.rect, pointer);

    dropIndicator = {
      targetId: over.id,
      position: edge,
      orientation,
    };
    lastOver = over;
  }

  function handleDragEnd({ active: activeEvent }: DragEndEvent) {
    // We use the lastOver instead of the Over object in DragEndEvent, so users can have a predictable behaviour
    // that item will move to where the indicator is pointing at instead of some random area at drag end.
    const finalOver = lastOver;
    const finalPosition = dropIndicator.position;

    if (!activeEvent || !finalOver) return;

    // A drop is only valid if there is a target and it's not the item itself.
    const success = finalOver && activeEvent.id !== finalOver.id;
    // Encapsulate the microtask logic within the provider.
    // This gives dnd-kit-svelte time to reset its internal state (isDragging)
    // before we notify the parent to update the UI.
    queueMicrotask(() => {
      if (success) {
        onDndEnd({
          active: activeEvent,
          over: finalOver,
          // The dropIndicator already holds the most accurate position ('top', 'bottom', etc.).
          position: finalPosition,
        });
      }
      active = null;
      clearIndicator();
    });

    // Reset state after the drag operation concludes.
    active = null;
    clearIndicator();
  }

  // Note that handleDragCancel only fires when user press ESC to cancel
  function handleDragCancel() {
    queueMicrotask(() => {
      active = null;
      clearIndicator();
    });
  }
</script>

<DndContext
  {sensors}
  onDragStart={handleDragStart}
  onDragMove={handleDragMove}
  onDragEnd={handleDragEnd}
  onDragCancel={handleDragCancel}
  {collisionDetection}
>
  {@render children({ dropIndicator })}

  <DragOverlay {dropAnimation}>
    {@render dragOverlay({ active })}
  </DragOverlay>
</DndContext>

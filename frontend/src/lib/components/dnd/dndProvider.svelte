<script lang="ts">
  import {
    DndContext,
    DragOverlay,
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

  // --- Type Definitions ---
  export type DropIndicatorState = {
    targetId: string | number | null;
    position: "before" | "after";
    orientation: "horizontal" | "vertical";
  };

  export type DndEndCallbackDetail = {
    active: Active;
    over: Over;
    position: DropIndicatorState["position"];
  };

  type Props = {
    children: Snippet<[{ dropIndicator: DropIndicatorState; active: Active | null }]>;
    dragOverlay: Snippet<[{ active: Active | null }]>;
    onDndEnd: (detail: DndEndCallbackDetail) => void;
    orientation?: "horizontal" | "vertical";
  };

  // --- Component Props ---
  let { children, dragOverlay, onDndEnd, orientation = "horizontal" }: Props = $props();

  // --- Svelte 5 State (Runes) ---
  let active = $state<Active | null>(null);
  let dropIndicator = $state<DropIndicatorState>({
    targetId: null,
    position: "before",
    orientation,
  });

  // FIX: Define the input sensors. This is what enables dragging.
  const sensors = useSensors(
    useSensor(MouseSensor, {
      // Require the mouse to move by 5px before starting a drag
      // activationConstraint: {
      // 	distance: 5
      // }
    }),
    useSensor(TouchSensor, {
      // Press delay of 250ms, with a tolerance of 5px of movement
    }),
  );

  // Define a null drop animation to prevent the "fly back" effect.
  const dropAnimation: DropAnimation | null = null;

  // --- Drag Handlers ---
  function handleDragStart(event: DragStartEvent) {
    active = event.active;
    dropIndicator = { targetId: null, position: "before", orientation };
  }

  function handleDragOver({ active: activeEvent, over }: DragOverEvent) {
    // This logic is now simple and correct. It reports what it sees.
    if (!over || !over.rect || !activeEvent.rect.translated) {
      dropIndicator = { targetId: null, position: "before", orientation };
      return;
    }
    const midpoint = over.rect.top + over.rect.height / 2;
    const isAfter = activeEvent.rect.translated.top > midpoint;

    dropIndicator = {
      targetId: over.id,
      position: isAfter ? "after" : "before",
      orientation,
    };
  }

  function handleDragEnd({ active: activeEvent, over }: DragEndEvent) {
    if (over && activeEvent.id !== over.id) {
      // Encapsulate the microtask logic within the provider.
      // This gives dnd-kit-svelte time to reset its internal state (isDragging)
      // before we notify the parent to update the UI.
      queueMicrotask(() => {
        onDndEnd({
          active: activeEvent,
          over,
          position: dropIndicator.position,
        });
      });
    }
    active = null;
    dropIndicator = { targetId: null, position: "before", orientation };
  }

  function handleDragCancel() {
    active = null;
    dropIndicator = { targetId: null, position: "before", orientation };
  }
</script>

<DndContext
  {sensors}
  onDragStart={handleDragStart}
  onDragOver={handleDragOver}
  onDragEnd={handleDragEnd}
  onDragCancel={handleDragCancel}
>
  {@render children({ dropIndicator, active })}

  <DragOverlay {dropAnimation}>
    {@render dragOverlay({ active })}
  </DragOverlay>
</DndContext>

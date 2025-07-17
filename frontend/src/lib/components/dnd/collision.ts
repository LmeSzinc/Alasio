import type { ClientRect, Collision, CollisionDetection, Data } from "@dnd-kit-svelte/core";

// well, we can't import this, so here's a manual definition
// import type { Coordinates } from "@dnd-kit-svelte/utilities";
export type Coordinates = {
  x: number;
  y: number;
};

/**
 * @interface ClosestEdgeOptions
 * Configuration for the closest edge collision detection algorithm.
 */
export interface ClosestEdgeOptions {
  orientation: "vertical" | "horizontal";
  /**
   * A map defining which active types can be dropped onto which droppable types.
   * Key: The `type` of the droppable element (`over.type`).
   * Value: An array of `type`s of the active draggable element (`active.type`) that are accepted.
   * If not provided, no type checking will occur, and all drops are considered possible.
   * @example { group: ['item', 'group'], item: ['item'] }
   */
  accepts?: Record<string, string[]>;
}

/**
 * @interface CollisionDataWithEdge
 * Defines the shape of the data that will be injected into the collision object.
 */
export interface CollisionWithEdge extends Collision {
  // The closest edge that was detected.
  edge: "top" | "bottom" | "left" | "right";
  // The calculated distance to that edge. This is used for sorting.
  distance: number;
}

/**
 * Calculates the distance from a point to a 1D line segment.
 * This is a helper function to determine distance even when the pointer is outside a projection.
 * @param point The coordinate of the pointer.
 * @param start The start coordinate of the line segment.
 * @param end The end coordinate of the line segment.
 * @returns The distance from the point to the segment.
 */
function distanceToSegment(point: number, start: number, end: number): number {
  if (point < start) return start - point;
  if (point > end) return point - end;
  return 0;
}

/**
 * Calculates the vertical distance from a pointer to a rectangle's boundary.
 * It prioritizes top/bottom edges if the pointer is within the horizontal bounds of the rect.
 * Otherwise, it calculates the Manhattan distance to the nearest vertical corner.
 * @returns An object containing the distance and the closest vertical edge ('top' or 'bottom').
 */
export function verticalDistance(rect: ClientRect, pointer: Coordinates): { distance: number; edge: "top" | "bottom" } {
  const isWithinHorizontal = pointer.x >= rect.left && pointer.x <= rect.right;
  const distTop = Math.abs(pointer.y - rect.top);
  const distBottom = Math.abs(pointer.y - rect.bottom);

  // If pointer is directly above or below the element, the distance is purely vertical.
  if (isWithinHorizontal) {
    return distTop < distBottom ? { distance: distTop, edge: "top" } : { distance: distBottom, edge: "bottom" };
  }

  // If outside, calculate the combined distance to the nearest corner.
  const dy = distanceToSegment(pointer.y, rect.top, rect.bottom);
  const dx = Math.min(Math.abs(pointer.x - rect.left), Math.abs(pointer.x - rect.right));

  return {
    distance: dx + dy,
    // The dominant edge is still determined by the vertical position.
    edge: pointer.y < rect.top ? "top" : "bottom",
  };
}

/**
 * Calculates the horizontal distance from a pointer to a rectangle's boundary.
 * It prioritizes left/right edges if the pointer is within the vertical bounds of the rect.
 * Otherwise, it calculates the Manhattan distance to the nearest horizontal corner.
 * @returns An object containing the distance and the closest horizontal edge ('left' or 'right').
 */
export function horizontalDistance(
  rect: ClientRect,
  pointer: Coordinates,
): { distance: number; edge: "left" | "right" } {
  const isWithinVertical = pointer.y >= rect.top && pointer.y <= rect.bottom;
  const distLeft = Math.abs(pointer.x - rect.left);
  const distRight = Math.abs(pointer.x - rect.right);

  // If pointer is directly to the left or right, the distance is purely horizontal.
  if (isWithinVertical) {
    return distLeft < distRight ? { distance: distLeft, edge: "left" } : { distance: distRight, edge: "right" };
  }

  // If outside, calculate the combined distance to the nearest corner.
  const dx = distanceToSegment(pointer.x, rect.left, rect.right);
  const dy = Math.min(Math.abs(pointer.y - rect.top), Math.abs(pointer.y - rect.bottom));

  return {
    distance: dx + dy,
    // The dominant edge is still determined by the horizontal position.
    edge: pointer.x < rect.left ? "left" : "right",
  };
}

/**
 * A Higher-Order Function that creates a "closest edge" collision detection algorithm.
 * This is the main export used to generate the collision detection logic.
 *
 * @param {ClosestEdgeOptions} options - Configuration for the algorithm.
 * @returns {CollisionDetection} A configured collision detection function to be used by DndContext.
 */
export const createClosestEdgeCollision = (options: ClosestEdgeOptions): CollisionDetection => {
  return (args) => {
    const { active, collisionRect, droppableContainers, droppableRects, pointerCoordinates } = args;

    // Try to get pointer coordinates, with fallback for keyboard navigation
    let pointer = pointerCoordinates;

    // If no pointer coordinates (common with keyboard navigation),
    // use the active element's center as fallback
    if (!pointer && collisionRect) {
      pointer = {
        x: collisionRect.left + collisionRect.width / 2,
        y: collisionRect.top + collisionRect.height / 2,
      };
    }

    // If we still don't have coordinates, we can't perform collision detection
    if (!pointer) {
      console.warn("No pointer coordinates available for collision detection");
      return [];
    }

    const collisions: CollisionWithEdge[] = [];

    for (const droppable of droppableContainers) {
      // Skip self-collision (don't drop on yourself)
      if (droppable.id === active.id) {
        continue;
      }

      // If `accepts` rules are provided, perform type checking.
      if (options.accepts) {
        const activeType = active.data?.type as string | undefined;
        const droppableType = droppable.data?.type as string | undefined;

        // If either element lacks a type, it cannot satisfy the rules.
        if (!activeType || !droppableType) continue;

        // Check if the droppable's type accepts the active's type.
        const acceptedTypes = options.accepts[droppableType];
        if (!acceptedTypes || !acceptedTypes.includes(activeType)) {
          continue; // Skip this droppable if the rule is not met.
        }
      }

      const rect = droppableRects.get(droppable.id);
      if (!rect) continue;

      // Based on the configured orientation, call the appropriate distance function.
      const { distance, edge } =
        options.orientation === "vertical" ? verticalDistance(rect, pointer) : horizontalDistance(rect, pointer);

      collisions.push({
        id: droppable.id,
        data: {
          ...droppable.data?.current,
        },
        // The 'value' is now the raw distance, used for sorting.
        distance: distance,
        edge: edge,
      });
    }

    // Sort by distance in ASCENDING order. The smallest distance is the best match.
    return collisions.sort((a, b) => {
      // Guard against undefined data, though our logic should prevent this.
      if (!a.data || !b.data) return 0;
      return a.distance - b.distance;
    });
  };
};

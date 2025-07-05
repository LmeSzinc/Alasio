// src/lib/ws/utils.ts

type Key = string | number;

/**
 * Performs an in-place deep set on an object or array.
 * Creates path segments if they don't exist. This function mutates the object directly.
 * @param obj The target object or array to modify.
 * @param path An array of keys representing the path.
 * @param value The value to set at the path.
 */
export function deepSet(obj: any, path: Key[], value: any): void {
	if (path.length === 0) {
    // This case implies replacing the object and should be handled by the caller.
		return;
	}

	let current = obj;
	for (let i = 0; i < path.length - 1; i++) {
		const key = path[i];

		if (current[key] === undefined || typeof current[key] !== 'object' || current[key] === null) {
			const nextKey = path[i + 1];
			// Create an array if the next key is a number, otherwise create an object.
			current[key] = typeof nextKey === 'number' ? [] : {};
		}
		current = current[key];
	}

	current[path[path.length - 1]] = value;
}

/**
 * Performs an in-place deep delete on an object or array.
 * This function mutates the object directly.
 * @param obj The target object or array to modify.
 * @param path An array of keys representing the path to the property to delete.
 */
export function deepDel(obj: any, path: Key[]): void {
	if (path.length === 0) {
		return; // Deleting with an empty path is a no-op.
	}

	let current = obj;
	// Traverse to the parent of the target property.
	for (let i = 0; i < path.length - 1; i++) {
		const key = path[i];
		// If the path doesn't exist, there's nothing to delete.
		if (current[key] === undefined || typeof current[key] !== 'object' || current[key] === null) {
			return;
		}
		current = current[key];
	}

	// Delete the target property from its parent.
	delete current[path[path.length - 1]];
}
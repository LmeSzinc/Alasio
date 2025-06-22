/**
 * A type-level check to see if a type is a plain object.
 * It excludes arrays and functions, which are technically `object`s.
 */
type IsPlainObject<T> = T extends Record<string, any>
  ? T extends any[] | ((...args: any[]) => any)
    ? false
    : true
  : false;

/**
 * [Type-level] Merges two object types, T and U, deeply.
 * U's properties have precedence over T's.
 */
type DeepMergeTwoTypes<T, U> =
  // Omit properties from T that are also in U, to give U precedence.
  Omit<T, keyof U> & {
    // Iterate over each key in U
    [K in keyof U]: K extends keyof T // If key exists in both T and U
      ? IsPlainObject<T[K]> extends true // and if the property in T is an object
        ? IsPlainObject<U[K]> extends true // and if the property in U is also an object
          ? DeepMergeTwoTypes<T[K], U[K]> // Then, recursively merge them
          : U[K] // Otherwise, U's property type wins
        : U[K] // Otherwise, U's property type wins
      : U[K]; // If key is only in U, use its type
  };

/**
 * [Type-level] Merges a list of object types from left to right.
 * The right-most type has the highest precedence.
 *
 * It uses recursion on the tuple of types.
 */
type DeepMergeAll<T extends readonly any[]> = T extends [infer First, ...infer Rest]
  ? Rest extends []
    ? First // Base case: If only one item is left, return it.
    : DeepMergeTwoTypes<First, DeepMergeAll<Rest>> // Recursive step: Merge the first item with the merged result of the rest.
  : {}; // Base case: If the array is empty, return an empty object.

/**
 * Checks if a value is a mergeable plain object.
 * This is a high-performance check that excludes null, arrays, and other non-object types.
 * @param item The value to check.
 * @returns `true` if the item is a plain object, otherwise `false`.
 */
function isObject(item: any): item is Record<string, any> {
	// `item !== null` is slightly faster than `item && ...`
	// as it avoids a truthiness conversion.
	return item !== null && typeof item === 'object' && !Array.isArray(item);
}

/**
 * [Core Engine - Not for direct export]
 * Deeply merges a source object into a target object by mutating the target.
 * @param target The target object, which will be mutated.
 * @param source The source object.
 */
function mergeInto(target: Record<string, any>, source: Record<string, any>): void {
	// Iterate over the keys of the source object using for...in for good performance.
	for (const key in source) {
		// Using Object.prototype.hasOwnProperty.call is the safest way to check for own properties.
		if (Object.prototype.hasOwnProperty.call(source, key)) {
			const sourceValue = source[key];
			const targetValue = target[key];

			// The core recursive condition: recurse only if both the target and source
			// values for the same key are objects.
			if (isObject(targetValue) && isObject(sourceValue)) {
				// Recurse directly on the target's sub-object to achieve in-place mutation.
				mergeInto(targetValue, sourceValue);
			} else {
				// Otherwise, the source value directly overwrites the target value.
				target[key] = sourceValue;
			}
		}
	}
}

/**
 * Performs a high-performance deep merge of one or more objects and returns a new,
 * deeply-typed object.
 *
 * - **Immutability**: Always returns a new object.
 * - **Robustness**: Ignores any non-object inputs.
 * - **Type-Safe**: Provides precise type inference for the merged result.
 *
 * @param sources One or more source objects to merge.
 * @returns A new object with a precisely inferred type representing the merged sources.
 */
export function deepMerge<T extends readonly any[]>(
	...sources: T
  ): DeepMergeAll<[...T]> { // The return type is now the result of our type-level magic
	const result: Record<string, any> = {};
  
	for (const source of sources) {
	  if (isObject(source)) {
		mergeInto(result, source);
	  }
	}
  
	// We cast to `any` because the runtime logic correctly produces the complex type,
	// but TypeScript can't verify this on its own without our explicit return type assertion.
	return result as any;
  }
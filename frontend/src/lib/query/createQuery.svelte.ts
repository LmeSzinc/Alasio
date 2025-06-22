import type { ApiResponse } from './client';
import type { TResponseMap, Query } from './queryState';
import { QueryLoadingState, QueryResponseState } from './queryState';

/**
 * Creates a reactive query object for Svelte 5 that handles race conditions and cancellation.
 * @param queryFn The asynchronous function that performs the data fetching. Its first argument must be an AbortSignal.
 * @param abortOutdated A boolean flag to enable or disable the race condition handling logic.
 * @returns A reactive query object with a `call` method to trigger fetching.
 */
export function createQuery<TMap extends TResponseMap, TArgs extends any[]>(
    queryFn: (signal: AbortSignal, ...args: TArgs) => Promise<ApiResponse<TMap[keyof TMap]>>,
    abortOutdated: boolean
): Query<TMap, TArgs> {

    const initialLoadingState = new QueryLoadingState();

    let queryState = $state<QueryLoadingState | QueryResponseState<TMap>>(initialLoadingState);
    let callArgs = $state<TArgs | undefined>(undefined);

    // This state variable serves as both a version counter and a trigger for the $effect.
    let requestVersion = $state(0);

    let abortController: AbortController | null = null;

    const refetchFn = (...args: TArgs) => {
        // If cancellation is enabled and a request is in-flight, abort it.
        if (abortOutdated && abortController) {
            abortController.abort();
        }

        callArgs = args;
        // Incrementing the version triggers the $effect.
        requestVersion++;
    };

    $effect(() => {
        // The effect is now dependent on `requestVersion`.
        // It won't run on initial mount (version 0).
        if (requestVersion === 0) return;

        // Capture the version for this specific request.
        const currentVersion = requestVersion;

        if (abortOutdated) {
            abortController = new AbortController();
        }
        const signal = abortController?.signal;

        async function fetchData() {
            if (!callArgs) return;

            // Only update the UI to a loading state if this is the latest request,
            // or if the cancellation feature is disabled.
            if (!abortOutdated || currentVersion === requestVersion) {
                queryState = initialLoadingState;
            }

            try {
                // Ensure a valid signal is passed, even if cancellation is off.
                const effectiveSignal = signal ?? new AbortController().signal;
                const response = await queryFn(effectiveSignal, ...callArgs!);

                // CRITICAL CHECK: Only update state if this response corresponds to the latest request.
                if (abortOutdated && currentVersion !== requestVersion) {
                    console.log(`Ignored stale response for version ${currentVersion}. Current is ${requestVersion}.`);
                    return;
                }

                // Update the state with the response data.
                queryState = new QueryResponseState(
                    response.success,
                    response.status,
                    response.data
                );
                if (abortOutdated) abortController = null;

            } catch (error: any) {
                // Handle abort errors gracefully.
                if (abortOutdated && error.name === 'AbortError') {
                    console.log(`Request version ${currentVersion} was aborted.`);
                    return;
                }

                // Only show an error if it's from the latest request (or if cancellation is off).
                if (!abortOutdated || currentVersion === requestVersion) {
                    queryState = new QueryResponseState(false, 0, { message: 'Fetch error' } as any);
                    if (abortOutdated) abortController = null;
                }
            }
        }

        fetchData();
    });

    // A Proxy creates a unified virtual object.
    // It combines the `call` method with the reactive properties from `queryState`.
    return new Proxy({}, {
        get(target, prop) {
            if (prop === 'call') return refetchFn;
            // @ts-ignore - This pattern is clean for proxying a state object.
            return queryState[prop];
        }
    }) as Query<TMap, TArgs>;
}
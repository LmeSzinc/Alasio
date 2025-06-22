export type TResponseMap = { [status: number]: any };

/**
 * Represents the "Loading" state of a query.
 */
export class QueryLoadingState {
    public readonly isLoading = true as const;
    public readonly isSuccess = false as const;
    public readonly isError = false as const;
    public readonly data = null;
    public readonly status = null;

    public is(status?: number): false {
        return false;
    }
}

/**
 * Represents a completed (Success or Error) state of a query.
 */
export class QueryResponseState<TMap extends TResponseMap> {
    public readonly isLoading = false as const;
    public readonly isSuccess: boolean;
    public readonly isError: boolean;
    public readonly data: TMap[keyof TMap];
    public readonly status: number;

    constructor(success: boolean, status: number, data: TMap[keyof TMap]) {
        this.isSuccess = success;
        this.isError = !success;
        this.status = status;
        this.data = data;
    }

    /**
     * The powerful type guard method.
     * `this` is now correctly used within a class member.
     */
    public is<S extends keyof TMap>(status: S): this is { data: TMap[S]; status: S } & this {
        return this.status === status;
    }
}

/**
 * The final, public-facing Query object type.
 * It's a union of our state classes and includes the `.call()` method.
 */
export type Query<TMap extends TResponseMap, TArgs extends any[]> = 
    { call: (...args: TArgs) => void }
    & (QueryLoadingState | QueryResponseState<TMap>);

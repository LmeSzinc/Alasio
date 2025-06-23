import { client, type ApiResponse } from './client';
import { createQuery } from './createQuery.svelte';
import { deepMerge } from './merge';
import type { TResponseMap, Query } from './queryState';

// --- Type-level Utilities ---

/**
 * [Type-level] Extracts the parameters of a function type.
 * Returns an empty tuple if T is not a function.
 */
type FnParams<T> = T extends (...args: infer P) => any ? P : [];

/**
 * [Type-level] Safely gets the response map from a config, providing a default.
 * If TConfig has a responseModel, use it; otherwise, default to `{ 200: unknown }`.
 */
type GetResponseMap<TConfig extends ApiConfig> = 
    TConfig extends { responseModel: infer R }
        ? R
        : { 200: unknown };

/**
 * A branded type to represent a type-level error message.
 * This is used to provide descriptive errors to the developer in their IDE.
 */
type TypeLevelError<T extends string> = { readonly __error: T } & {};


// --- The Public Contract Interface ---

/**
 * Defines the public-facing contract for a fully configured API endpoint.
 * This simple interface is what developers will see in their IDEs after calling `.build()`,
 * hiding the complex internal builder types and methods.
 */
export interface ApiEndpoint<
    TArgs extends any[],
    TResponseMapFromConfig extends TResponseMap
> {
    /**
     * Makes a one-off API call. The arguments are determined by the API definition,
     * prioritizing the `.caller()` method's signature.
     */
    call(...args: TArgs): Promise<ApiResponse<TResponseMapFromConfig>>;

    /**
     * Creates a reactive Svelte 5 query object. The returned `query.call()` 
     * method has the same typed arguments as the endpoint's `call` method.
     */
    createQuery(): Query<TResponseMapFromConfig, TArgs>;
}

// --- Internal Configuration and Argument Calculation ---

/**
 * The internal configuration object that the ApiBuilder accumulates.
 */
type ApiConfig = {
    method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
    url: string | ((...args: any[]) => string);
    options?: RequestInit;
    requestModel?: any;
    responseModel?: TResponseMap;
    callerFn?: (...args: any[]) => any;
    abortOutdated?: boolean;
};

/**
 * [Type-level] This is the core generic type that calculates the final arguments for `.call()`.
 * It gives the highest priority to the `.caller()` function's signature and provides a
 * descriptive error message for invalid configurations.
 */
type CallArgs<TConfig extends ApiConfig> =
    // 1. If a .caller() function is defined, its parameters are the source of truth.
    'callerFn' extends keyof TConfig
    ? FnParams<TConfig['callerFn']>

    // 2. If no .caller() is defined, fall back to the previous logic.
    : TConfig['url'] extends (...args: any[]) => any
        ? 'requestModel' extends keyof TConfig
            // This combination is invalid. Instead of `never`, return a descriptive error type.
            ? [
                  ERROR: TypeLevelError<
                      "API DEFINITION ERROR: When the URL is a function and a request body is defined, you MUST use the .caller() method to resolve the ambiguity."
                  >
              ]
            // 3. If URL is a function and no request model, use the URL function's parameters.
            : FnParams<TConfig['url']>
    // 4. If URL is a static string and there is a request model, the arg is the request payload.
    : 'requestModel' extends keyof TConfig
        ? [payload: TConfig['requestModel']]
        // 5. If URL is a static string and no request model, there are no arguments.
        : [];

// --- The Builder Class ---

class ApiBuilder<TConfig extends ApiConfig> {
    // The constructor is protected to force creation via the public static `create` method.
    protected constructor(protected readonly config: TConfig) {}

    /**
     * [Internal Factory] Creates the initial ApiBuilder instance.
     * This method is generic to preserve the precise type of the URL for later inference.
     */
    public static create<TUrl extends ApiConfig['url']>(
        method: ApiConfig['method'],
        url: TUrl
    ) {
        const config = { method, url, abortOutdated: true };
        return new ApiBuilder(config);
    }

    /**
     * Sets static fetch options (e.g., headers, cache policy) for the request.
     * These will be deep-merged with any other options.
     */
    withOptions<O extends RequestInit>(options: O): ApiBuilder<TConfig & { options: O }> {
        const newConfig = { ...this.config, options };
        return new ApiBuilder(newConfig);
    }

    /**
     * Defines the TypeScript type of the request body.
     * This is used for type safety when constructing the request in `.caller()` or as the default payload.
     * Usage: .request<{ id: number; name: string; }>()
     */
    request<T>(): ApiBuilder<TConfig & { requestModel: T }> {
        const newConfig = { ...(this.config as any), requestModel: null as unknown as T };
        return new ApiBuilder<TConfig & { requestModel: T }>(newConfig);
    }

    /**
     * Defines the TypeScript types for possible responses.
     * Can be a single model (maps to status 200) or a map of status codes to models.
     * Usage: .response<Post[]>() or .response<{ 200: Post, 404: Error }>()
     */
    response<T>(): ApiBuilder<TConfig & { responseModel: T }> {
        const newConfig = { ...(this.config as any), responseModel: null as unknown as T };
        return new ApiBuilder<TConfig & { responseModel: T }>(newConfig);
    }

    /**
     * Defines a custom business logic layer that maps the final `.call()` arguments
     * to the underlying HTTP request parameters. This method determines the final
     * signature of `.call()`.
     * 
     * @param fn A function whose arguments will become the arguments for `.call()`.
     *           It must return an object mapping to the request parts. IDE will provide hints for the return type.
     */
    caller<
        T extends (...args: any[]) => {
            urlArgs?: any[];
            body?: any;
            options?: RequestInit;
        }
    >(fn: T): ApiBuilder<TConfig & { callerFn: T }> {
        const newConfig = { ...(this.config as any), callerFn: fn };
        return new ApiBuilder<TConfig & { callerFn: T }>(newConfig);
    }

    /**
     * Enables or disables the outdated request cancellation feature.
     * It is enabled by default for safety.
     * @param enabled - Set to `false` to disable this feature.
     */
    abortOutdated(enabled: boolean): ApiBuilder<TConfig & { abortOutdated: boolean }> {
        const newConfig = { ...this.config, abortOutdated: enabled };
        return new ApiBuilder(newConfig);
    }

    /**
     * Finalizes the API definition and returns a clean, ready-to-use endpoint.
     * This should be the last call in the chain to get the final, usable object.
     */
    build(): ApiEndpoint<CallArgs<TConfig>, GetResponseMap<TConfig>> {
        // This method returns the instance of itself, but cast to the clean `ApiEndpoint` interface.
        // This "hides" the builder methods (.request, .caller, etc.) from the final product's type signature,
        // providing a clean and simple API surface for the end-user.
        return this as unknown as ApiEndpoint<CallArgs<TConfig>, GetResponseMap<TConfig>>;
    }

    // These methods are available on the builder but are intended to be accessed
    // via the clean interface returned by `.build()`.
    private call = (...args: CallArgs<TConfig>): Promise<ApiResponse<GetResponseMap<TConfig>>> => {
        const controller = new AbortController();
        return this.buildAndFetch.bind(this)(controller.signal, ...args);
    };

    private createQuery = (): Query<GetResponseMap<TConfig>, CallArgs<TConfig>> => {
        const boundQueryFn = this.buildAndFetch.bind(this);
        const shouldAbort = this.config.abortOutdated ?? true;
        
        return createQuery(
            boundQueryFn as (signal: AbortSignal, ...args: CallArgs<TConfig>) => Promise<ApiResponse<any>>,
            shouldAbort
        );
    };
    
    private buildAndFetch = async (...args: any[]): Promise<ApiResponse<GetResponseMap<TConfig>>> => {
        const signal = args.shift() as AbortSignal; 
        const { url, method, options = {}, callerFn } = this.config;
        
        let finalUrl: string;
        let finalBody: any = undefined;
        let callerOptions: RequestInit = {};

        if (callerFn) {
            const result = callerFn(...args) || {};
            finalUrl = typeof url === 'function' ? url(...(result.urlArgs || [])) : url;
            finalBody = result.body;
            callerOptions = result.options || {};
        } else {
            if (typeof url === 'function') {
                finalUrl = url(...args);
            } else {
                finalUrl = url;
                if ('requestModel' in this.config) {
                    finalBody = args[0];
                }
            }
        }

        const baseOptions: RequestInit = { method, signal };

        // Safely add body and content-type header only if a body exists.
        // This prevents overwriting user-defined headers with an empty object.
        if (finalBody) {
            baseOptions.body = JSON.stringify(finalBody);
            baseOptions.headers = { 'Content-Type': 'application/json' };
        }

        const fetchOptions = deepMerge(
            baseOptions,
            options,
            callerOptions
        );

        return client.request<GetResponseMap<TConfig>>(finalUrl, fetchOptions);
    };
}

// --- The Public Entry Point ---

/**
 * The main entry point for defining a new API endpoint.
 * It uses the static factory method on ApiBuilder to start the chain.
 */
export const withQuery = {
    /**
     * Starts building a GET request.
     * @param url A static URL string or a function that builds a URL from parameters.
     */
    get: <T extends string | ((...args: any[]) => string)>(url: T) => ApiBuilder.create('GET', url),
    
    /**
     * Starts building a POST request.
     * @param url A static URL string or a function that builds a URL from parameters.
     */
    post: <T extends string | ((...args: any[]) => string)>(url: T) => ApiBuilder.create('POST', url),

    /**
     * Starts building a PUT request.
     * @param url A static URL string or a function that builds a URL from parameters.
     */
    put: <T extends string | ((...args: any[]) => string)>(url: T) => ApiBuilder.create('PUT', url),

    /**
     * Starts building a DELETE request.
     * @param url A static URL string or a function that builds a URL from parameters.
     */
    delete: <T extends string | ((...args: any[]) => string)>(url: T) => ApiBuilder.create('DELETE', url),

    /**
     * Starts building a PATCH request.
     * @param url A static URL string or a function that builds a URL from parameters.
     */
    patch: <T extends string | ((...args: any[]) => string)>(url: T) => ApiBuilder.create('PATCH', url),
};
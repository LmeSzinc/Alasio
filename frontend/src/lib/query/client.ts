import { deepMerge } from './merge';

type PossibleData<TResponseMap> = TResponseMap[keyof TResponseMap];
/**
 * A smart, type-safe wrapper for the native Fetch Response.
 * It standardizes both successful and failed responses and provides a convenient type guard.
 */
class SmartResponse<TResponseMap> {
	public readonly success: boolean;
	public readonly status: number;
	public readonly data: PossibleData<TResponseMap>;

	constructor(success: boolean, status: number, data: PossibleData<TResponseMap>) {
		this.success = success;
		this.status = status;
		this.data = data;
	}

	/**
	 * A type guard to safely narrow the response data based on the status code.
	 * @param status The HTTP status code to check for.
	 */
	is<S extends keyof TResponseMap>(status: S): this is { data: TResponseMap[S]; status: S } {
		return this.status === status;
	}
}

export type ApiResponse<TData> = SmartResponse<TData>;

const BASE_URL = '/api';

const GLOBAL_FETCH_OPTIONS: RequestInit = {
	headers: { 'Accept': 'application/json' }
};

/**
 * The core HTTP request function. It never throws but always returns an ApiResponse.
 * This turns network errors into predictable, type-safe return values.
 * It will re-throw a specific error on abort, which is handled by the query layer.
 */
async function http<TData>(url: string, options: RequestInit): Promise<ApiResponse<TData>> {
	let response: Response;
	try {
		response = await fetch(url, options);
	} catch (e: any) {
		// Critical: Identify abort errors and re-throw them silently.
		// This allows the upper layer (createQuery) to catch and ignore them.
		if (e.name === 'AbortError') {
			throw e;
		}
		console.error('HTTP Client Error - Network Error:', e);
		const errorPayload = { message: 'Network error' } as TData;
		// status 0 for client-side errors
		return new SmartResponse<TData>(false, 0, errorPayload as PossibleData<TData>);
	}
	// Empty response
	const bodyIsEmpty = response.headers.get('content-length') === '0' || response.status === 204;
    if (bodyIsEmpty) {
        return new SmartResponse<TData>(response.ok, response.status, null as any);
    }

	try {
		// success
		const data = await response.json();
		return new SmartResponse<TData>(response.ok, response.status, data);
	} catch (e: any) {
		// Critical: Identify abort errors and re-throw them silently.
		// This allows the upper layer (createQuery) to catch and ignore them.
		if (e.name === 'AbortError') {
			throw e;
		}
		console.error('HTTP Client Error - Invalid JSON Response:', e);
		const errorPayload = { message: 'Invalid JSON response' } as TData;
		return new SmartResponse<TData>(false, response.status, errorPayload as PossibleData<TData>);
	}
}

export const client = {
	request: <TData>(path: string, options: RequestInit) => {
		const finalOptions = deepMerge(GLOBAL_FETCH_OPTIONS, options);
		return http<TData>(`${BASE_URL}${path}`, finalOptions);
	}
};
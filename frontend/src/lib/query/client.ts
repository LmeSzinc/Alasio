import { deepMerge } from './merge';

/**
 * A smart, type-safe wrapper for the native Fetch Response.
 * It standardizes both successful and failed responses and provides a convenient type guard.
 */
class SmartResponse<TData> {
	public readonly success: boolean;
	public readonly status: number;
	public readonly data: TData;

	constructor(success: boolean, status: number, data: TData) {
		this.success = success;
		this.status = status;
		this.data = data;
	}

	/**
	 * A type guard to safely narrow the response data based on the status code.
	 * @param status The HTTP status code to check for.
	 */
	is<S extends number>(status: S): this is { data: TData extends { [key in S]: infer D } ? D : never; status: S } {
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
	try {
		const response = await fetch(url, options);
		const data = response.status === 204 ? null : await response.json();

		return new SmartResponse<TData>(response.ok, response.status, data);

	} catch (e: any) {
		// Critical: Identify abort errors and re-throw them silently.
		// This allows the upper layer (createQuery) to catch and ignore them.
		if (e.name === 'AbortError') {
			throw e;
		}

		console.error('HTTP Client Error:', e);
		const errorPayload = { message: 'Network error or invalid JSON response' } as TData;
		return new SmartResponse<TData>(false, 0, errorPayload); // status 0 for client-side errors
	}
}

export const client = {
	request: <TData>(path: string, options: RequestInit) => {
		const finalOptions = deepMerge(GLOBAL_FETCH_OPTIONS, options);
		return http<TData>(`${BASE_URL}${path}`, finalOptions);
	}
};

import type { ArgData } from "$lib/components/arg/utils.svelte";

export interface DashboardArgData extends ArgData {
  value: Record<string, ArgData>;
}

export const DEFAULT_TIME = "2020-01-01T00:00:00Z";
export const DEFAULT_TIME_DISPLAY = "2020-01-01 00:00:00";
export const DEFAULT_TIME_MS = new Date(DEFAULT_TIME).getTime();

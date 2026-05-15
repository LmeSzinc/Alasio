import type { ArgData, InfoData } from "../arg/utils.svelte";

export type DashboardGroupInfo = InfoData & {
  dashboard: string;
  dashboard_color?: string;
};
export type DashboardGroupData = {
  _info?: DashboardGroupInfo;
} & {
  [K in string as K extends "_info" ? never : K]: ArgData;
};

export const DEFAULT_TIME = "2020-01-01T00:00:00Z";
export const DEFAULT_TIME_DISPLAY = "2020-01-01 00:00:00";
export const DEFAULT_TIME_MS = new Date(DEFAULT_TIME).getTime();

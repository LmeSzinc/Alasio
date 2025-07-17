// Minimal type requirements
export type ConfigLike = {
  id: number;
  name: string;
  mod: string;
  gid: number;
  iid: number;
  [key: string]: any; // Allow any other properties
};
// Topic data from "ConfigScan"
export type ConfigTopicLike = Record<string, ConfigLike>;

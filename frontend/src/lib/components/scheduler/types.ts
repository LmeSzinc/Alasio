import type { RestartTopicLike } from "$lib/components/aside/types";

export type TaskItem = {
  TaskName: string;
  NextRun: number | string;
};

export type TaskQueueData = {
  pending: TaskItem[];
  waiting: TaskItem[];
};

export type TaskRunningData = string | null; // null = no task running

export type TaskQueueI18n = {
  [task_name: string]: string;
};

// Phase of a graceful backend restart: `null` = no restart in progress
export type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

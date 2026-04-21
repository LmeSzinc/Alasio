export type TaskItem = {
  TaskName: string;
  NextRun: number | string;
};

export type TaskQueueData = {
  running: string | null;
  pending: TaskItem[];
  waiting: TaskItem[];
};

export type TaskQueueI18n = {
  [task_name: string]: string;
};

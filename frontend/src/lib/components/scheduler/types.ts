export type TaskItem = {
  TaskName: string;
  NextRun: number | string;
};

export type TaskQueueData = {
  running: TaskItem | null;
  pending: TaskItem[];
  waiting: TaskItem[];
};

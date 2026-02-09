export type TaskItem = {
  TaskName: string;
  NextRun: number | string;
};

export type TaskQueueData = {
  running: string | null;
  pending: TaskItem[];
  waiting: TaskItem[];
};

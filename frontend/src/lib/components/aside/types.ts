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

// Topic data from "Restart": the phase of a graceful backend restart.
// Absent (no restart in progress) = the object is empty / undefined.
// 'done' is transient: it is pushed right before the topic is cleared.
export type RestartTopicLike = {
  phase?: "stopping" | "shutting-down" | "resuming" | "done";
  update?: number;
};

// idle: not running
// starting: requesting to start a worker, starting worker process
// running: worker process running
// scheduler-stopping: requesting to stop scheduler loop, worker will stop after current task
// scheduler-waiting: worker waiting for next task, no task running currently
// killing: requesting to kill a worker, worker will stop and do GC asap
// force-killing: requesting to kill worker process immediately
// disconnected: backend just lost connection worker,
//   worker process will be clean up and worker status will turn into idle or error very soon
// error: worker stopped with error
//   Note that scheduler will loop forever, so there is no "stopped" state
//   If user request "scheduler_stopping" or "killing", state will later be "idle"
// restarting: worker stopped because of a graceful backend restart, the backend
//   will auto-resume it after the restart; manual start is rejected
// resuming: queued for auto-resume after the backend restart (no process yet)
//   a stop on it cancels the auto-resume
export type WORKER_STATE =
  | "idle"
  | "starting"
  | "running"
  | "disconnected"
  | "error"
  | "scheduler-stopping"
  | "scheduler-waiting"
  | "killing"
  | "force-killing"
  | "restarting"
  | "resuming";

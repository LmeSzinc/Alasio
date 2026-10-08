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

// States of a mod update (the Update topic, one entry per mod):
// - 'unmanaged': the mod declares no update source, it is never checked
// - 'idle': never checked, or the checks wait for a manual request
// - 'checking': a check is in flight (cancellable through update_cancel)
// - 'uptodate': the local version is the latest one
// - 'available': an update is available and waits for the user
// - 'downloading': the update transaction downloads the update into memory
//   (nothing on disk changes, cancellable)
// - 'updating': the transaction applies the update (stop / replace / restart /
//   resume), local and irreversible, not cancellable
// - 'error': the last check or the update failed, see error
export type UPDATE_STATE =
  "unmanaged" | "idle" | "checking" | "uptodate" | "available" | "downloading" | "updating" | "error";

// One update entry of the Update topic (the value type of the record).
export type UpdateInfoLike = {
  state: UPDATE_STATE;
  // version of the local index pack of the mod, '' when it is missing
  current_version?: string;
  // latest version seen by the last succeeded check, '' when unknown
  latest_version?: string;
  // unix timestamp of the last check that finished, 0 = never
  checked_at?: number;
  // failure message of the last error, '' when there is none
  error?: string;
};

// Topic data from "Update": the update state of every mod, keyed by mod name.
export type UpdateTopicLike = Record<string, UpdateInfoLike>;

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
// updating: the same as restarting, but the restart belongs to an update
//   transaction (the update manager set its instance window before the restart
//   began): shown as 更新中 / updating instead of restarting
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
  | "updating"
  | "resuming";

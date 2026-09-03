/**
 * Bounded startup log buffer (main process side of the loading page log
 * channel).
 *
 * The loading page cannot register its IPC listener until the renderer
 * mounted, while the backend starts streaming log lines immediately after
 * spawn. A plain fire-and-forget push (webContents.send) does not queue
 * for unregistered listeners, so every line produced before the
 * subscription would be lost - including the error lines of a startup
 * failure. The buffer makes the channel lossless: lines are recorded
 * here, and the loading page replays them in full when it subscribes.
 *
 * Design (doc/2026-09-03_electron-window-flicker-and-loading-page.md
 * §4.2):
 * - chronological order (oldest first); the renderer reverses the
 *   snapshot for its newest-first view
 * - bounded: STARTUP_LOG_LIMIT lines, the oldest line is dropped when the
 *   cap is exceeded
 * - subscribe() is the one-way transition into push mode: it marks the
 *   buffer as subscribed and returns a full snapshot copy. Lines recorded
 *   after it are both buffered and pushed by the caller on the same IPC
 *   channel, which is ordered after the snapshot reply - no loss, no
 *   duplication (every subscription replays the full current buffer, so
 *   a renderer reload is idempotent too)
 * - clear() empties the buffer. Called at the start of every launch
 *   attempt (a retry must not mix the previous attempt's lines) and
 *   after a successful settle; NOT called on failure/timeout so the
 *   error tail stays replayable until the next attempt or exit.
 */
const STARTUP_LOG_LIMIT = 500;

export class StartupLogBuffer {
  private lines: string[] = [];
  private subscribed = false;

  /**
   * Whether a renderer subscribed (push mode is on).
   *
   * Returns:
   *     bool: True once subscribe() was called at least once
   */
  get isSubscribed(): boolean {
    return this.subscribed;
  }

  /**
   * Record one line (chronological). When the buffer is full the oldest
   * line is dropped.
   *
   * Args:
   *     line (str): One complete log line, without trailing newline
   */
  record(line: string): void {
    this.lines.push(line);
    if (this.lines.length > STARTUP_LOG_LIMIT) {
      this.lines.shift();
    }
  }

  /**
   * Transition to the subscribed (push) mode and return the current
   * buffer. Idempotent: every call returns the full current snapshot, so
   * re-subscribing after a renderer reload replays everything buffered so
   * far.
   *
   * Returns:
   *     list[str]: Buffered lines in chronological order (oldest first),
   *         as a copy - mutating the returned array does not affect the
   *         buffer
   */
  subscribe(): string[] {
    this.subscribed = true;
    return this.lines.slice();
  }

  /**
   * Empty the buffer. The subscribed flag is kept: after a clear (retry)
   * new lines are still pushed to the already-subscribed renderer.
   */
  clear(): void {
    this.lines = [];
  }
}

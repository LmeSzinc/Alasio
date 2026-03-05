class GlobalClock {
  #now = $state(Date.now());
  #intervalMs: number;
  #timerId: ReturnType<typeof setInterval> | undefined = undefined;
  #useCount = 0;

  constructor(intervalMs = 1000) {
    this.#intervalMs = intervalMs;
  }

  // 1. Use a getter to expose the state as read-only
  get now() {
    return this.#now;
  }

  // 2. Stable reference for the event handler (using an arrow function to auto-bind 'this')
  #handleVisibilityChange = () => {
    if (document.hidden) {
      this.#clearTimer();
    } else {
      this.#startTimer();
    }
  };

  // 3. Internal start logic
  #startTimer() {
    if (this.#timerId || document.hidden) return;

    // Sync time immediately to prevent perceived lag when switching back from the background
    this.#now = Date.now();
    this.#timerId = setInterval(() => {
      this.#now = Date.now();
    }, this.#intervalMs);
  }

  // 4. Internal cleanup logic
  #clearTimer() {
    if (this.#timerId) {
      clearInterval(this.#timerId);
      this.#timerId = undefined;
    }
  }

  /**
   * Use this method within components.
   * Automates the lifecycle:
   * - Starts when the first component mounts.
   * - Stops when the last component unmounts.
   * - Automatically pauses when the page is in the background.
   */
  use() {
    $effect(() => {
      // Increment reference count
      if (this.#useCount === 0) {
        // Register global listener only when the first consumer joins
        document.addEventListener("visibilitychange", this.#handleVisibilityChange);
        this.#startTimer();
      }
      this.#useCount++;

      // Return cleanup logic
      return () => {
        this.#useCount--;
        if (this.#useCount <= 0) {
          // Perform full cleanup when there are no remaining consumers
          this.#clearTimer();
          document.removeEventListener("visibilitychange", this.#handleVisibilityChange);
        }
      };
    });
  }
}

// Export as a singleton
export const globalClock = new GlobalClock(1000);

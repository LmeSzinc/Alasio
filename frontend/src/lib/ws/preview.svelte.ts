import { WebsocketManager } from "./client.svelte";

/**
 * PreviewManager extends WebsocketManager to handle the "Preview" topic with high-performance binary updates.
 * For the "Preview" topic, the backend sends raw bytes directly to optimize screenshot transmission.
 */
export class PreviewManager extends WebsocketManager {
  #previewHeader = new TextEncoder().encode("Preview");

  constructor() {
    super({
      defaultSubscriptions: ["Preview"],
    });
  }

  protected override getWsUrl(): string {
    const url = new URL("/api/preview", window.location.href);
    url.protocol = url.protocol.replace("http", "ws");
    return url.toString();
  }

  /**
   * Overrides the default message handling to catch raw binary preview messages.
   * If a message starts with the "Preview" header bytes, it is treated as a direct update to the "Preview" topic.
   * Otherwise, it delegates to the base implementation.
   */
  protected onMessage(event: MessageEvent<ArrayBuffer>) {
    const data = event.data;
    const view = new Uint8Array(data);

    // Optimized check for the "Preview" binary header
    if (view.length >= this.#previewHeader.length) {
      let isPreview = true;
      for (let i = 0; i < this.#previewHeader.length; i++) {
        if (view[i] !== this.#previewHeader[i]) {
          isPreview = false;
          break;
        }
      }

      if (isPreview) {
        // Set and delete the topic data to trigger Svelte's reactivity, but not store it in the manager.
        this.topics["Preview"] = data;
        // delete this.topics["Preview"];
        return;
      }
    }

    // Fall back to standard JSON/ping-pong handling for other messages
    super.onMessage(event);
  }
}

// Singleton instance for the application to use
export const previewClient = new PreviewManager();

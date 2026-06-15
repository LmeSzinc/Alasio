import { WebsocketManager } from "./client.svelte";

/**
 * PreviewManager extends WebsocketManager to handle the "Preview" topic with high-performance binary updates.
 * For the "Preview" topic, the backend sends raw bytes directly to optimize screenshot transmission.
 */
export class PreviewManager extends WebsocketManager {
  #previewHeader = new TextEncoder().encode("Preview");

  protected override getWsUrl(): string {
    const url = new URL("/api/preview", window.location.href);
    url.protocol = url.protocol.replace("http", "ws");
    return url.toString();
  }

  /**
   * Overrides the default message handling to catch raw binary preview messages.
   * If a message starts with the "Preview" bytes (all known headers share this prefix),
   * it is treated as a direct update to the "Preview" topic.
   * Otherwise, it delegates to the base implementation.
   */
  protected onMessage(event: MessageEvent<ArrayBuffer>) {
    const data = event.data;
    const view = new Uint8Array(data);

    // All known preview headers (Preview_ / PreviewS) share the first 7 bytes "Preview"
    if (view.length >= this.#previewHeader.length) {
      let isPreview = true;
      for (let i = 0; i < this.#previewHeader.length; i++) {
        if (view[i] !== this.#previewHeader[i]) {
          isPreview = false;
          break;
        }
      }

      if (isPreview) {
        // Set the topic data to trigger Svelte's reactivity
        this.topics["Preview"] = data;
        return;
      }
    }

    // Fall back to standard JSON/ping-pong handling for other messages
    super.onMessage(event);
  }
}

// Singleton instance for the application to use
export const previewClient = new PreviewManager();

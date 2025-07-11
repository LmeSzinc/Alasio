import { browser } from "$app/environment";
import { goto, invalidateAll } from "$app/navigation";
import { deepDel, deepSet } from "./deep";
import type { RequestEvent, ResponseEvent } from "./event";
import { createRpc, type RpcCallbacks, type RpcOptions } from "./rpc.svelte";

/**
 * Configuration options for the WebsocketManager.
 */
interface WebsocketManagerOptions {
  /** Extend the list of topics that are always subscribed to. */
  defaultSubscriptions?: string[];
  /** Define topics that should be handled as capped, scrollable arrays. Key is topic name, value is max length. */
  scrollTopics?: Record<string, number>;
}

// --- Base configurations ---
const BASE_DEFAULT_SUBSCRIPTIONS = ["error"];
const BASE_SCROLL_TOPICS = { log: 1000 };

class WebsocketManager {
  // --- State Management (Svelte 5 Runes) ---
  connectionState = $state<"connecting" | "open" | "closed" | "reconnecting">("closed");
  topics = $state<Record<string, any>>({});

  // --- Private properties ---
  #ws: WebSocket | null = null;
  subscriptions = $state<Record<string, number>>({});
  #rpcCallbacks = new Map<string, { onSuccess: (v: string) => void; onError: (v: string) => void }>();

  #messageQueue: RequestEvent[] = [];
  #reconnectAttempts = 0;
  #reconnectTimeout: number | undefined = undefined;
  #encoder = new TextEncoder();
  #decoder = new TextDecoder();
  #options: Required<WebsocketManagerOptions>;

  constructor(options: WebsocketManagerOptions = {}) {
    if (!browser) {
      this.#options = { defaultSubscriptions: [], scrollTopics: {} };
      return;
    }

    // Merge user-provided options with base configurations.
    this.#options = {
      defaultSubscriptions: [...BASE_DEFAULT_SUBSCRIPTIONS, ...(options.defaultSubscriptions || [])],
      scrollTopics: { ...BASE_SCROLL_TOPICS, ...(options.scrollTopics || {}) },
    };
  }

  /**
   * Constructs the WebSocket URL from the current window location.
   */
  #getWsUrl(): string {
    const url = new URL("/api/ws", window.location.href);
    url.protocol = url.protocol.replace("http", "ws");
    return url.toString();
  }

  /**
   * Initiates a WebSocket connection if one is not already open or connecting.
   */
  connect() {
    if (this.#ws && (this.#ws.readyState === WebSocket.OPEN || this.#ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.connectionState = this.#reconnectAttempts > 0 ? "reconnecting" : "connecting";

    try {
      this.#ws = new WebSocket(this.#getWsUrl());
      this.#ws.binaryType = "arraybuffer";
    } catch (e) {
      console.error("Failed to create WebSocket:", e);
      this.connectionState = "closed";
      this.#scheduleReconnect();
      return;
    }

    this.#ws.onopen = () => {
      this.connectionState = "open";
      this.#reconnectAttempts = 0;
      clearTimeout(this.#reconnectTimeout);

      // Resubscribe to all topics that have active component subscriptions.
      for (const topic in this.subscriptions) {
        if (this.subscriptions[topic] > 0) {
          this.#send({ t: topic });
        }
      }

      // Send any messages that were queued while disconnected.
      while (this.#messageQueue.length > 0) {
        const message = this.#messageQueue.shift();
        if (message) this.#send(message);
      }
    };

    this.#ws.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      // Handle server heartbeats.
      const message = this.#decoder.decode(event.data);
      if (message === "ping") {
        this.#ws?.send(this.#encoder.encode("pong"));
        return;
      }

      // Handle data events.
      try {
        const data: ResponseEvent = JSON.parse(message);
        this.#handleEvent(data);
      } catch (e) {
        console.error("Failed to parse WebSocket message:", message, e);
      }
    };

    this.#ws.onclose = (event: CloseEvent) => {
      console.warn(`WebSocket closed: code=${event.code}, reason=${event.reason}`);
      this.connectionState = "closed";
      this.#ws = null;

      // Decide action based on the close code.
      if (event.code === 4001) {
        // Custom code for Authentication failure
        console.error("Authentication failed. Redirecting to login via goto().");
        this.#clearAll();
        goto("/auth/login");
        return;
      }
      if (event.code >= 4000) {
        // Assume other 4xxx codes mean unrecoverable server error
        console.error("Unrecoverable server error. Invalidating all data via invalidateAll().");
        this.#clearAll();
        invalidateAll(); // SvelteKit's way of refreshing page data.
        return;
      }

      // For all other cases (e.g., normal closure, network issues), attempt to reconnect.
      this.#scheduleReconnect();
    };

    this.#ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };
  }

  /**
   * Processes a single event from the server, routing it to the correct handler.
   */
  #handleEvent(data: ResponseEvent) {
    // 1. Check if it's an RPC response.
    if (data.i) {
      if (this.#rpcCallbacks.has(data.i)) {
        const callbacks = this.#rpcCallbacks.get(data.i)!;
        // Backend contract: if 'v' is present, it's an error string. Otherwise, success.
        if (data.v) {
          callbacks.onError(String(data.v));
        } else {
          callbacks.onSuccess("");
        }
        // The operation itself is responsible for unregistering the callback via its cleanup function.
        return; // Handled. Stop processing.
      } else {
        // It has an ID, but we're no longer waiting for it (e.g., timed out). Discard silently.
        return;
      }
    }

    // 2. If not an RPC response, handle as a standard data event.
    // Apply defaults based on the backend spec for omitted fields.
    const { t: topic, o: op = "add", k: keys = [], v: value = null } = data;

    // Discard messages for topics we are not subscribed to.
    if (!this.#isSubscribed(topic)) {
      return;
    }

    const maxLines = this.#options.scrollTopics[topic];
    if (maxLines) {
      // --- High-performance path for scroll topics (e.g., logs) ---
      if (op === "full") {
        this.topics[topic] = Array.isArray(value) ? value : [];
      } else if (op === "add") {
        const logArray = this.topics[topic];
        if (!Array.isArray(logArray)) {
          this.topics[topic] = [value]; // Initialize if it's the first log entry.
        } else {
          logArray.push(value);
          if (logArray.length > maxLines) {
            logArray.shift(); // Efficiently remove the oldest entry.
          }
        }
      }
      // `set` and `del` operations are intentionally ignored for scroll topics.
      return;
    }

    // --- Generic path for standard topics ---
    switch (op) {
      case "full":
        this.topics[topic] = value;
        break;
      case "add":
      case "set":
        if (keys.length === 0) {
          this.topics[topic] = value;
        } else {
          if (this.topics[topic] === undefined) {
            this.topics[topic] = {};
          }
          // Mutate in-place. Svelte 5's $state detects deep mutations.
          deepSet(this.topics[topic], keys, value);
        }
        break;
      case "del":
        if (this.topics[topic] !== undefined && keys.length > 0) {
          deepDel(this.topics[topic], keys);
        }
        break;
    }
  }
  #clearAll() {
    // Clear all topic data to prevent displaying stale information.
    for (const key in this.topics) {
      delete this.topics[key];
    }
  }

  /**
   * Checks if the client is currently subscribed to a given topic.
   */
  #isSubscribed(topic: string): boolean {
    return this.#options.defaultSubscriptions.includes(topic) || (this.subscriptions[topic] || 0) > 0;
  }

  /**
   * Manages the reconnection logic with exponential backoff.
   */
  #scheduleReconnect() {
    if (this.#reconnectAttempts >= 5) {
      console.warn("Max reconnect attempts reached. Invalidating all data via invalidateAll().");
      invalidateAll(); // Force a data refresh as a last resort.
      return;
    }
    const delay = Math.min(1000 * 2 ** this.#reconnectAttempts, 30000);
    this.#reconnectAttempts++;
    this.#reconnectTimeout = setTimeout(() => this.connect(), delay);
  }

  /**
   * Sends a payload to the WebSocket server or queues it if disconnected.
   */
  #send(payload: RequestEvent) {
    if (this.#ws?.readyState === WebSocket.OPEN) {
      try {
        const message = JSON.stringify(payload);
        this.#ws.send(this.#encoder.encode(message));
      } catch (e) {
        console.error("Failed to serialize or send message:", payload, e);
      }
    } else {
      // Queue the message if the connection is not open.
      this.#messageQueue.push(payload);
      this.connect();
    }
  }

  // --- Implementation of RpcContext ---
  registerRpcCall(id: string, callbacks: RpcCallbacks) {
    this.#rpcCallbacks.set(id, callbacks);
  }
  unregisterRpcCall(id: string) {
    this.#rpcCallbacks.delete(id);
  }
  hasRpcCall(id: string): boolean {
    return this.#rpcCallbacks.has(id);
  }

  /**
   * Subscribes to a topic and returns a client object for interaction.
   */
  sub(topic: string, forceSend: boolean = false) {
    const currentCount = this.subscriptions[topic] || 0;
    // CRITICAL: Update the subscription count *before* initiating connection logic.
    // This prevents a race condition where `onopen` could fire before the count is updated.
    this.subscriptions[topic] = currentCount + 1;

    // Ensure a connection is active or being established.
    this.connect();

    // If this is the first subscription for this topic AND the connection is already open,
    // we must send the 'sub' message immediately. If the connection is not open,
    // the `onopen` handler is responsible for sending the initial subscription message.
    // For testing purposes, `forceSend` allows bypassing this check.
    if (forceSend || (currentCount === 0 && !this.#options.defaultSubscriptions.includes(topic))) {
      if (this.#ws?.readyState === WebSocket.OPEN) {
        this.#send({ t: topic });
      }
    }
    const client = this;

    return {
      /**
       * A reactive getter for the topic's data.
       * It uses the captured `instance` to access the correct `topics` state.
       */
      get data() {
        return client.topics[topic];
      },

      /**
       * Creates a stateful RPC handler for this topic.
       */
      // The implementation signature also uses the interface.
      rpc(options?: RpcOptions) {
        return createRpc(topic, client, options);
      },
    };
  }
  /**
   * Unsubscribes to a topic.
   * Call this in a component's `onDestroy` to clean up the subscription.
   */
  unsub(topic: string, forceSend: boolean = false) {
    if (this.#options.defaultSubscriptions.includes(topic)) return;

    const currentCount = this.subscriptions[topic] || 0;

    // Decrement the subscription count if it's greater than 0
    if (currentCount > 0) {
      this.subscriptions[topic] = currentCount - 1;
    }

    // Determine if we should send the 'unsub' message to the backend
    // Send if forceSend is true, OR if this is the last component unsubscribing (count becomes 0)
    const shouldSendUnsubMessage = forceSend || (currentCount === 1 && !forceSend);

    if (shouldSendUnsubMessage) {
      this.#send({ t: topic, o: "unsub" });
    }

    // If the subscription count is now 0, clean up the topic data
    if (this.subscriptions[topic] <= 0) {
      delete this.subscriptions[topic];
      delete this.topics[topic];
    }
  }
  unsubAll = () => {
    for (const topic in this.subscriptions) {
      this.unsub(topic);
    }
  };

  /**
   * A raw send method for direct use, e.g., in a testing UI.
   */
  sendRaw(payload: RequestEvent) {
    this.#send(payload);
  }

  /**
   * Returns the list of default subscriptions.
   */
  getDefaultSubscriptions(): string[] {
    return this.#options.defaultSubscriptions;
  }
}

// --- Singleton Instantiation ---
// The client is instantiated once and configured here for the entire application.
export const websocketClient = new WebsocketManager({
  // Example of extending configuration:
  // scrollTopics: { 'custom_log': 500 },
  // defaultSubscriptions: ['audit_trail']
});

export type TopicClient = ReturnType<typeof websocketClient.sub>;

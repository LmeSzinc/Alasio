import { browser } from "$app/environment";
import { goto, invalidateAll } from "$app/navigation";
import { deepDel, deepSet } from "./deep";
import type { RequestEvent, ResponseEvent } from "./event";
import { RenewalCoordinator } from "./renewal.svelte";
import { routeState } from "./route-state.svelte";
import { type RpcCallbacks, type RpcOptions, createRpc } from "./rpc.svelte";

/**
 * Configuration options for the WebsocketManager.
 */
interface WebsocketManagerOptions {
  /** Extend the list of topics that are always subscribed to. */
  defaultSubscriptions?: string[];
  /** Define topics that should be handled as capped, scrollable arrays. Key is topic name, value is max length. */
  scrollTopics?: Record<string, number>;
  /**
   * Verify the login session over HTTP: the status of 'GET /api/auth/renew'
   * (200 = still logged in, 401/403 = refused), or 0 when the request could
   * not reach a backend at all (inconclusive). Injected by tests; the
   * production default is the real endpoint.
   */
  sessionProbe?: () => Promise<number>;
}

// --- Base configurations ---
const BASE_DEFAULT_SUBSCRIPTIONS = ["ConnState"];
const BASE_SCROLL_TOPICS = { Log: 500 };

// A browser never abandons a websocket handshake on its own: a connection
// that neither opens nor fails (e.g. one stranded in the backlog of a backend
// that is exiting, while its replacement binds the same port) would leave the
// client waiting in "connecting" forever, with no reconnect ever scheduled.
// The watchdog drops such an attempt so the regular retry path takes over.
const CONNECT_TIMEOUT = 15_000;

// Backoff of the session probe while the backend cannot be reached (a restart
// in progress): the last value is repeated, so the probe resolves the question
// as soon as the new process answers, whatever the outage lasted.
const SESSION_PROBE_DELAYS = [1000, 2000, 4000, 8000, 15000, 30000];

/**
 * The login layer's refusal, as an explicit signal: close code + reason of a
 * handshake whose credentials are not accepted (see
 * alasio/backend/ws/ws_server.py, pinned by tests/ws_fixtures/messages.json).
 *
 * Only this reason ends the session — every other refused handshake, the code
 * 4001 included, is a refusal of that one connection: the backend may have
 * been restarting, an old process may have refused the connection while it was
 * shutting down, or an admission rule may have applied. Those keep the page
 * and reconnect (the login state is confirmed over HTTP in that case).
 */
export const WS_CLOSE_AUTH_FAILED = 4001;
export const WS_CLOSE_AUTH_FAILED_REASON = "alasio:auth-failed";

/**
 * In-flight session probe, shared by every ws client of the page (the preview
 * client inherits the same auth-failure handler): the login state is a
 * property of the session, not of a connection, so one request answers for
 * both instead of two clients asking the same question.
 */
let sessionProbeInFlight: Promise<number> | undefined;

function probeSession(probe: () => Promise<number>): Promise<number> {
  if (sessionProbeInFlight === undefined) {
    sessionProbeInFlight = probe().finally(() => {
      sessionProbeInFlight = undefined;
    });
  }
  return sessionProbeInFlight;
}

/**
 * Default session probe: 'GET /api/auth/renew' is the login layer's own check
 * (it validates the JWT cookie and refreshes it), so its status is the
 * authoritative answer to "is this session still logged in?".
 *
 * Returns:
 *     Promise<number>: The HTTP status, or 0 when no backend could be
 *         reached (unreachable, aborted): inconclusive, not a refusal.
 */
async function defaultSessionProbe(): Promise<number> {
  if (typeof fetch !== "function") return 0;
  try {
    const response = await fetch("/api/auth/renew", { credentials: "same-origin" });
    return response.status;
  } catch (e) {
    // The backend is not answering (restarting, down): unknown, not refused.
    console.warn("Session probe could not reach the backend, retrying...");
    return 0;
  }
}

export class WebsocketManager {
  // --- State Management (Svelte 5 Runes) ---
  connectionState = $state<"connecting" | "open" | "closed" | "reconnecting">("closed");
  topics = $state<Record<string, any>>({});
  /**
   * A counter that increments each time the websocket successfully connects.
   * This is a monotonically increasing "session ID" for the connection,
   * crucial for resilient operations to detect reconnections and re-fetch data.
   * It never resets during the application's lifecycle.
   */
  connectionGeneration = $state(0);

  /**
   * Tracks which topics have received their initial 'full' message,
   * serving as a stable signal for subscription readiness.
   * Key is topic name, value is boolean. This is decoupled from the topic data itself
   * to avoid triggering effects on high-frequency data updates.
   */
  topicReady = $state<Record<string, boolean>>({});

  // --- Private properties ---
  #ws: WebSocket | null = null;
  subscriptions = $state<Record<string, number>>({});
  #rpcCallbacks = new Map<string, { onSuccess: (v: string) => void; onError: (v: string) => void }>();

  #messageQueue: RequestEvent[] = [];
  #reconnectAttempts = 0;
  #reconnectTimeout: ReturnType<typeof setTimeout> | undefined = undefined;
  #connectTimeout: ReturnType<typeof setTimeout> | undefined = undefined;
  #probeTimeout: ReturnType<typeof setTimeout> | undefined = undefined;
  #probeAttempt = 0;
  #encoder = new TextEncoder();
  #decoder = new TextDecoder();
  #options: Required<WebsocketManagerOptions>;

  constructor(options: WebsocketManagerOptions = {}) {
    if (!browser) {
      this.#options = { defaultSubscriptions: [], scrollTopics: {}, sessionProbe: defaultSessionProbe };
      return;
    }

    // Merge user-provided options with base configurations.
    this.#options = {
      defaultSubscriptions: [...BASE_DEFAULT_SUBSCRIPTIONS, ...(options.defaultSubscriptions || [])],
      scrollTopics: { ...BASE_SCROLL_TOPICS, ...(options.scrollTopics || {}) },
      sessionProbe: options.sessionProbe || defaultSessionProbe,
    };

    // Initialize cache for default subscriptions.
    for (const topic of this.#options.defaultSubscriptions) {
      if (this.topics[topic] === undefined && this.#options.scrollTopics[topic]) {
        this.topics[topic] = [];
      }
    }
  }

  /**
   * Constructs the WebSocket URL from the current window location.
   * Can be overridden by subclasses to connect to different endpoints.
   */
  protected getWsUrl(): string {
    const url = new URL("/api/ws", window.location.href);
    url.protocol = url.protocol.replace("http", "ws");
    return url.toString();
  }

  /**
   * Initiates a WebSocket connection if one is not already open or connecting.
   */
  connect() {
    // Never connect while a (public) route is mounted: the backend
    // accepts the handshake then closes it with 4001 (a refused handshake
    // would only surface as 1006 to the browser), so an unauthenticated
    // connection is a guaranteed failure plus an auth-failure redirect.
    // The (public) layout owns routeState.public; once a private page
    // mounts its components subscribe after the layout destroy hook
    // cleared the flag, and this method runs again to establish the
    // connection. A rejected session (close 4001) raises the same flag
    // ahead of that navigation, so every ws client keeps refusing to
    // connect until the login page is reached (see #handleAuthFailure).
    if (routeState.public) {
      // Drop any scheduled reconnect: retrying on a public page, or on a
      // session whose credentials were just refused, is pointless and
      // would burn the retry budget into invalidateAll().
      if (this.#reconnectTimeout !== undefined) {
        clearTimeout(this.#reconnectTimeout);
        this.#reconnectTimeout = undefined;
      }
      this.#reconnectAttempts = 0;
      this.connectionState = "closed";
      return;
    }

    if (this.#ws && (this.#ws.readyState === WebSocket.OPEN || this.#ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.connectionState = this.#reconnectAttempts > 0 ? "reconnecting" : "connecting";

    let ws: WebSocket;
    try {
      ws = new WebSocket(this.getWsUrl());
      ws.binaryType = "arraybuffer";
    } catch (e) {
      console.error("Failed to create WebSocket:", e);
      this.connectionState = "closed";
      this.#scheduleReconnect();
      return;
    }
    this.#ws = ws;
    this.#armConnectTimeout(ws);

    ws.onopen = () => {
      this.connectionState = "open";
      this.connectionGeneration++;
      this.#reconnectAttempts = 0;
      clearTimeout(this.#reconnectTimeout);
      // A live connection answers the session question by itself.
      this.#clearConnectTimeout();
      this.#clearProbeRetry();

      // Drop the topic data of the previous connection: a reconnect may land
      // on a NEW backend session (a graceful restart / crash recovery rebuilds
      // the backend process), whose state has nothing to do with the cached
      // one. The server only pushes a full snapshot for topics that HAVE data,
      // so a stale value would never be corrected (e.g. a restart phase stuck
      // at 'shutting-down', workers stuck at 'restarting'). The data is kept
      // for as long as the connection is down (status quo instead of a
      // flashing page) and only dropped here, right before the
      // re-subscriptions below refill every topic from the new session.
      this.#clearAll();

      // Immediately mark all default topics as "ready".
      for (const topic of this.#options.defaultSubscriptions) {
        this.topicReady[topic] = true;
      }

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

    ws.onmessage = (event: MessageEvent<ArrayBuffer>) => this.onMessage(event);

    ws.onclose = (event: CloseEvent) => {
      console.warn(`WebSocket closed: code=${event.code}, reason=${event.reason}`);
      this.connectionState = "closed";
      this.#ws = null;
      this.#clearConnectTimeout();

      // Decide action based on the close code.
      if (event.code === WS_CLOSE_AUTH_FAILED) {
        if (event.reason === WS_CLOSE_AUTH_FAILED_REASON) {
          // The backend said it explicitly: the credentials are not accepted,
          // the session has to log in again. No probing, no retrying — the
          // login page is the only place left for this session.
          console.error("Authentication failed: the credentials were refused, returning to the login page.");
          this.#endSession();
          return;
        }
        // A refused handshake without that signal is not a verdict on the
        // session (see #handleAuthFailure): the login layer is asked over HTTP
        // before anything is thrown away.
        console.error("Authentication failed: the handshake was refused, checking the session...");
        void this.#handleAuthFailure();
        return;
      }
      if (event.code === 4002) {
        // Electron token rotated and evicted: standard
        // reconnect only — the new handshake carries a fresh token. No
        // goto, no invalidateAll: refreshing the page on every rotation
        // would be an infinite refresh loop when tokens never match
        // (red line).
        console.warn("Electron token rotated, reconnecting...");
        this.#clearTopicReady();
        this.#scheduleReconnect();
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
      // Note that we don't clear topic data when the connection drops, so the
      // page keeps its state during a random disconnection instead of flashing;
      // the data is dropped on the next successful open (see onopen), so a
      // reconnect never leaves the previous session's data behind.
      this.#clearTopicReady();
      this.#scheduleReconnect();
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };
  }

  /**
   * Intentionally closes the current websocket connection and resets all
   * client state (topics, subscriptions, queued messages, rpc callbacks,
   * scroll buffers). Called when a (public) route mounts: public pages
   * must not hold a connection, and stale data must not leak into the
   * next private session.
   */
  disconnect() {
    // Cancel any pending reconnect, handshake watchdog and session probe,
    // and reset the retry budget.
    if (this.#reconnectTimeout !== undefined) {
      clearTimeout(this.#reconnectTimeout);
      this.#reconnectTimeout = undefined;
    }
    this.#clearConnectTimeout();
    this.#clearProbeRetry();
    this.#reconnectAttempts = 0;

    // Close the live connection without triggering the onclose reconnect
    // path: this is an intentional teardown, not a dropped connection.
    // All callbacks are detached so a close while the handshake is still
    // pending (CONNECTING) cannot fire onerror / onclose logs either.
    const ws = this.#ws;
    this.#ws = null;
    if (ws) {
      this.#detachSocket(ws);
      try {
        ws.close();
      } catch (e) {
        console.error("Failed to close WebSocket:", e);
      }
    }

    // Reset all data state.
    this.#clearAll();
    for (const topic in this.subscriptions) {
      delete this.subscriptions[topic];
    }
    this.#messageQueue = [];
    this.#rpcCallbacks.clear();
    this.#cancelScrollFlush();
    this.connectionState = "closed";
  }

  /**
   * Main entry point for processing incoming WebSocket messages.
   * Can be overridden by subclasses to handle specialized message formats.
   */
  protected onMessage(event: MessageEvent<ArrayBuffer>) {
    try {
      // Handle server heartbeats.
      const message = this.#decoder.decode(event.data);
      if (message === "ping") {
        this.#ws?.send(this.#encoder.encode("pong"));
        return;
      }

      // Handle data events.
      const data: ResponseEvent | ResponseEvent[] = JSON.parse(message);
      const events = Array.isArray(data) ? data : [data];

      // Group events by topic to perform batch updates
      const updates = new Map<string, ResponseEvent[]>();

      for (const item of events) {
        // 0. Control messages: the server
        // asks this connection to renew its electron token after a
        // rotation. Handled before RPC responses and topic data, the
        // message never touches topic state.
        if (item.t === "auth" && item.o === "full" && item.v === "renew") {
          renewalCoordinator.renew();
          continue;
        }
        // 1. Check if it's an RPC response.
        if (item.i) {
          this.#handleRpc(item);
          continue;
        }
        // 2. Group data events
        const topic = item.t;
        if (!updates.has(topic)) {
          updates.set(topic, []);
        }
        updates.get(topic)!.push(item);
      }

      // Apply updates per topic
      for (const [topic, items] of updates) {
        this.#handleTopicBatch(topic, items);
      }
    } catch (e) {
      console.error("Failed to parse WebSocket message:", e);
    }
  }

  /**
   * Handles RPC responses.
   */
  #handleRpc(data: ResponseEvent) {
    // 1. Check if it's an RPC response.
    if (data.i) {
      if (this.#rpcCallbacks.has(data.i)) {
        const callbacks = this.#rpcCallbacks.get(data.i)!;
        // Backend contract: if 'v' is present, it's an error string. Otherwise, success.
        if (data.v) {
          callbacks.onError(String(data.v));
        } else {
          callbacks.onSuccess(data.i);
        }
        // The operation itself is responsible for unregistering the callback via its cleanup function.
        return; // Handled. Stop processing.
      } else {
        // It has an ID, but we're no longer waiting for it (e.g., timed out). Discard silently.
        return;
      }
    }
  }

  #pendingScrollUpdates = new Map<string, ResponseEvent[]>();
  #flushHandle: number | null = null;

  /**
   * Processes a batch of events for a single topic.
   */
  #handleTopicBatch(topic: string, events: ResponseEvent[]) {
    // Discard messages for topics we are not subscribed to.
    if (!this.#isSubscribed(topic)) {
      return;
    }

    const maxLines = this.#options.scrollTopics[topic];
    if (maxLines) {
      // --- High-performance path for scroll topics (e.g., logs) ---
      // Buffer events and schedule a flush on the next animation frame.
      // This decouples the WebSocket reception rate from the render rate,
      // preventing the main thread from being blocked by excessive reactivity updates.
      if (!this.#pendingScrollUpdates.has(topic)) {
        this.#pendingScrollUpdates.set(topic, []);
      }
      const buffer = this.#pendingScrollUpdates.get(topic)!;
      buffer.push(...events);

      // Safety valve: prevent memory explosion if flush is delayed (e.g. background tab)
      // If the buffer grows too large, flush immediately regardless of the scheduler.
      if (buffer.length > 50) {
        this.#flushScrollUpdates();
        return;
      }

      if (this.#flushHandle === null) {
        // Use setTimeout when hidden to keep processing in background (RAF pauses in background)
        if (document.hidden) {
          this.#flushHandle = window.setTimeout(() => this.#flushScrollUpdates(), 50);
        } else {
          this.#flushHandle = requestAnimationFrame(() => this.#flushScrollUpdates());
        }
      }
      return;
    }

    // --- Generic path for standard topics ---
    for (const data of events) {
      const { o: op = "add", k: keys = [], v: value = null } = data;
      switch (op) {
        case "full":
          this.topics[topic] = value;
          break;
        case "add":
        case "set":
          if (keys.length === 0) {
            this.topics[topic] = value;
          } else {
            let topicData = this.topics[topic];
            if (topicData === undefined || topicData === null || typeof topicData !== "object") {
              // A path event implies the topic data is a container. Reset
              // non-object data (null or scalar) before applying the path.
              this.topics[topic] = typeof keys[0] === "number" ? [] : {};
              // Re-read through the proxy: the assignment stores a new
              // proxied container in the source, so mutating the raw object
              // above would bypass reactivity.
              topicData = this.topics[topic];
            }
            // Mutate in-place. Svelte 5's $state detects deep mutations.
            deepSet(topicData, keys, value);
          }
          break;
        case "del":
          if (keys.length > 0) {
            const topicData = this.topics[topic];
            if (topicData !== undefined && topicData !== null && typeof topicData === "object") {
              deepDel(topicData, keys);
            }
          }
          break;
      }
    }
  }

  /**
   * Cancels the pending scroll flush and drops the buffered updates.
   */
  #cancelScrollFlush() {
    if (this.#flushHandle !== null) {
      cancelAnimationFrame(this.#flushHandle);
      clearTimeout(this.#flushHandle);
      this.#flushHandle = null;
    }
    this.#pendingScrollUpdates.clear();
  }

  /**
   * Flushes buffered updates for scroll topics.
   * This runs at most once per frame (approx. 60fps).
   */
  #flushScrollUpdates() {
    this.#flushHandle = null;

    for (const [topic, events] of this.#pendingScrollUpdates) {
      const maxLines = this.#options.scrollTopics[topic];
      // Clone the array to avoid intermediate reactivity triggers
      let logArray = Array.isArray(this.topics[topic]) ? [...this.topics[topic]] : [];
      let changed = false;

      for (const event of events) {
        const { o: op = "add", v: value = null } = event;
        if (op === "full") {
          logArray = Array.isArray(value) ? value : [];
          changed = true;
        } else if (op === "add") {
          logArray.push(value);
          changed = true;
        }
      }

      if (changed) {
        // Enforce limit once per batch
        if (logArray.length > maxLines) {
          // Keep the last maxLines elements
          logArray.splice(0, logArray.length - maxLines);
        }
        this.topics[topic] = logArray;
      }
    }
    this.#pendingScrollUpdates.clear();
  }
  #clearAll() {
    // Clear all topic data to prevent displaying stale information.
    for (const key in this.topics) {
      delete this.topics[key];
    }
    this.#clearTopicReady();
  }
  #clearTopicReady() {
    // Clear the readiness state to ensure it's re-evaluated on next connect.
    for (const key in this.topicReady) {
      delete this.topicReady[key];
    }
  }

  /**
   * Handles a refused handshake that did not carry the explicit auth-failure
   * signal (WS_CLOSE_AUTH_FAILED_REASON).
   *
   * A refusal without that signal is not a verdict on the session: it can be
   * transient (the backend may still be starting after a graceful restart, an
   * old process may have refused the connection while it was shutting down,
   * the electron token of the host may not have been re-announced yet).
   * Navigating away on every refusal threw the page away (route, topic data,
   * unsent input) for a rejection the next second would not have repeated.
   *
   * The session's own authority is the HTTP login layer, so it is asked:
   * '/api/auth/renew' answers 200 while the session is valid, 401 when the
   * credentials are no longer accepted and 403 when this client is not
   * admitted at all. Only the latter two end the session. The third outcome —
   * the request reaching no backend at all (the restart window) — is
   * inconclusive: the page is left alone and the probe retries with a
   * backoff, so an outage never ends the session by itself. A 200 reconnects,
   * so the page keeps everything it had and the refused ticket is simply
   * presented to the new connection.
   *
   * Every ws client of the page inherits this handler (the preview client
   * included) and they share one probe; the navigation is left to the first
   * client that learns the session is over (a second goto would cancel the
   * first one).
   */
  async #handleAuthFailure() {
    // The refused connection will never deliver anything: drop the
    // readiness state so the re-subscriptions of the next connection refill
    // every topic and the resilient rpcs notice the new generation. The topic
    // data itself is kept (as on every dropped connection): the page shows its
    // state instead of flashing empty, and the next open replaces it.
    this.#clearTopicReady();
    this.connectionState = "reconnecting";

    const status = await probeSession(this.#options.sessionProbe);

    if (status === 200) {
      // The session is alive: the refusal was transient. Everything the page
      // holds stays, the connection is taken back.
      console.warn("The session is still valid, reconnecting...");
      this.#clearProbeRetry();
      this.#reconnectAttempts = 0;
      this.#scheduleReconnect();
      return;
    }

    if (status === 401 || status === 403) {
      // The login layer refused this client: the session is over.
      console.error("The session is over, returning to the login page.");
      this.#endSession();
      return;
    }

    // Inconclusive (the backend could not be reached: it is probably
    // restarting). Keep the page and ask again later.
    const delay = SESSION_PROBE_DELAYS[Math.min(this.#probeAttempt, SESSION_PROBE_DELAYS.length - 1)];
    this.#probeAttempt++;
    this.#cancelProbeTimer();
    this.#probeTimeout = setTimeout(() => void this.#handleAuthFailure(), delay);
  }

  /**
   * Ends the session: the login layer refused this client (401/403 over
   * HTTP, not just a refused handshake). Everything the dead session holds is
   * dropped, every ws client of the page is stopped (the login page is the
   * only place left for such a session) and the page is left to the login
   * page.
   */
  #endSession() {
    const navigate = !routeState.public;
    routeState.public = true;
    this.#clearProbeRetry();
    this.connectionState = "closed";
    // A session that can never come back must not burn the retry budget
    // into invalidateAll().
    if (this.#reconnectTimeout !== undefined) {
      clearTimeout(this.#reconnectTimeout);
      this.#reconnectTimeout = undefined;
    }
    this.#reconnectAttempts = 0;
    this.#clearAll();
    // Outgoing messages of the dead session are dropped, not queued: they
    // can never be answered, and replaying them into the next
    // (logged-in) session would re-run stale operations.
    this.#messageQueue = [];
    // Pending rpcs are unregistered so their timeout callbacks stay
    // silent (a timeout only toasts while its call is still registered):
    // the page is on its way to the login page, reporting per-call
    // failures of a session that is already over is noise.
    this.#rpcCallbacks.clear();
    this.#cancelScrollFlush();
    if (navigate) goto("/auth");
  }

  /**
   * Arms the handshake watchdog (see CONNECT_TIMEOUT): a socket that neither
   * opens nor fails is abandoned so the retry path takes over instead of
   * leaving the client in "connecting" forever.
   */
  #armConnectTimeout(ws: WebSocket) {
    this.#clearConnectTimeout();
    this.#connectTimeout = setTimeout(() => {
      this.#connectTimeout = undefined;
      // Only the attempt this timer was armed for may be abandoned, and only
      // while it is still handshaking (an open/closed socket is handled by its
      // own events).
      if (this.#ws !== ws || ws.readyState !== WebSocket.CONNECTING) return;
      console.warn("WebSocket handshake timed out, retrying...");
      // Detach before closing: the abandoned socket must not run the close
      // path on top of the retry scheduled here.
      this.#detachSocket(ws);
      try {
        ws.close();
      } catch (e) {
        // already gone
      }
      this.#ws = null;
      this.connectionState = "closed";
      this.#clearTopicReady();
      this.#scheduleReconnect();
    }, CONNECT_TIMEOUT);
  }

  #clearConnectTimeout() {
    if (this.#connectTimeout !== undefined) {
      clearTimeout(this.#connectTimeout);
      this.#connectTimeout = undefined;
    }
  }

  #clearProbeRetry() {
    this.#cancelProbeTimer();
    this.#probeAttempt = 0;
  }

  #cancelProbeTimer() {
    if (this.#probeTimeout !== undefined) {
      clearTimeout(this.#probeTimeout);
      this.#probeTimeout = undefined;
    }
  }

  /**
   * Detaches every handler of a socket, so a later event of an abandoned or
   * intentionally closed connection cannot run any of the client's paths.
   */
  #detachSocket(ws: WebSocket) {
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
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

    // Initialize cache for the topic if it doesn't exist.
    if (this.topics[topic] === undefined && this.#options.scrollTopics[topic]) {
      this.topics[topic] = [];
    }

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

      // If the subscription count is now 0, clean up the topic data
      if ((this.subscriptions[topic] || 0) <= 0) {
        delete this.subscriptions[topic];
        delete this.topics[topic];
        delete this.topicReady[topic];
      }
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

// Electron renewal coordinator bound to the ws client. The sendRaw closure
// is deferred so the singleton can be created before websocketClient is
// initialized (the closure only runs when a renewal submits).
export const renewalCoordinator = new RenewalCoordinator({
  sendRaw: (payload) => websocketClient.sendRaw(payload),
});

export type TopicClient = ReturnType<typeof websocketClient.sub>;

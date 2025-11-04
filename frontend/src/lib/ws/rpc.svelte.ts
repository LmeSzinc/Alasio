import { untrack } from "svelte";
import { toast } from "svelte-sonner";
import { websocketClient } from "./client.svelte";
import type { RequestEvent } from "./event";

/**
 * Defines the shape of the callback object used to handle operation results.
 */
export interface RpcCallbacks {
  onSuccess: (response: string) => void;
  onError: (errorMessage: string) => void;
}

/**
 * Optional callbacks that can be passed to the `call` method.
 */
export interface CallCallbacks {
  /**
   * A callback function that is executed when the RPC call succeeds.
   * @param response The success message or data returned from the server.
   *                 You can omit this parameter in your function if you don't need it.
   *                 Example: `onSuccess: () => console.log('Success!')`
   */
  onSuccess?: (response: string) => void;
  /**
   * A callback function that is executed when the RPC call fails.
   * @param errorMessage The error message describing what went wrong.
   *                     You can omit this parameter in your function if you don't need it.
   *                     Example: `onError: () => console.error('It failed!')`
   */
  onError?: (errorMessage: string) => void;
}

/**
 * The context object passed from WebsocketManager to createOperation, breaking the import cycle.
 * It provides a minimal, stable interface for the operation logic to interact with the client.
 */
export interface RpcContext {
  sendRaw(payload: RequestEvent): void;
  registerRpcCall(id: string, callbacks: RpcCallbacks): void;
  unregisterRpcCall(id: string): void;
  hasRpcCall(id: string): boolean;
}

/**
 * Configuration options for creating an Operation handler.
 */
export interface RpcOptions {
  /** Delay in milliseconds before showing the pending state to prevent UI flickering. */
  pendingDelay?: number;
  /** Custom timeout in milliseconds for this specific operation. */
  timeout?: number;
}

/**
 * The public API object for a single stateful operation instance.
 */
export interface Rpc {
  /** Reactive state indicating if the operation is currently in progress, respecting a delay. */
  readonly isPending: boolean;
  /** Holds the error message string from a failed operation. */
  readonly errorMsg: string | null;
  /** Holds the success message/ID from a successful operation. */
  readonly successMsg: string | null;
  /** Reactive state that can be bound to a dialog's visibility. */
  isOpen: boolean;
  /**
   * Sends the operation command to the server with a type-safe payload.
   */
  call(func: string, args: any, callbacks?: CallCallbacks): void;
  /** Manually resets the operation's state. */
  reset(): void;
  /** A helper method to reset state and open a bound dialog. */
  open(): void;
}

/**
 * Creates and manages the state for a single operation instance.
 * It's a factory function used by the TopicClient.
 * @private
 */
export function createRpc(
  topic: string,
  // It receives a context object instead of the full client instance to prevent circular imports.
  context: RpcContext,
  options: { pendingDelay?: number; timeout?: number } = {},
): Rpc {
  // These are Svelte 5 runes, usable directly because of the *.svelte.ts file extension.
  let isPending = $state(false);
  let errorMsg = $state<string | null>(null);
  let successMsg = $state<string | null>(null);
  let isOpen = $state(false);

  let operationTimeoutId: number | undefined;
  let pendingDelayTimeoutId: number | undefined;

  const PENDING_DELAY = options.pendingDelay ?? 300;
  const TIMEOUT = options.timeout ?? 5000;

  const reset = () => {
    isPending = false;
    errorMsg = null;
    successMsg = null;
    if (operationTimeoutId) clearTimeout(operationTimeoutId);
    if (pendingDelayTimeoutId) clearTimeout(pendingDelayTimeoutId);
  };

  const open = () => {
    reset();
    isOpen = true;
  };

  // Perform an RPC call
  const call = (func: string, args: any = {}, callbacks?: CallCallbacks) => {
    errorMsg = null;
    successMsg = null;

    // Schedule the pending state to appear only if the operation takes longer than PENDING_DELAY.
    pendingDelayTimeoutId = setTimeout(() => {
      isPending = true;
    }, PENDING_DELAY);

    // Generate a unique ID for this specific operation instance.
    const id = `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;

    const cleanupAndReset = () => {
      reset();
      context.unregisterRpcCall(id);
    };

    // Register callbacks with the central manager for this specific operation ID.
    context.registerRpcCall(id, {
      onSuccess: (response: string) => {
        cleanupAndReset();
        successMsg = response; // Typically the correlation ID.
        isOpen = false; // Automatically close the bound dialog on success.
        callbacks?.onSuccess?.(response);
      },
      onError: (errMessage: string) => {
        cleanupAndReset();
        errorMsg = errMessage;
        toast.error(`RPC call error on topic="${topic}", func="${func}"`, { description: errorMsg });
        callbacks?.onError?.(errMessage);
      },
    });

    // Set a timeout for the entire operation.
    operationTimeoutId = setTimeout(() => {
      const customTimeoutError = "RPC call timeout";
      if (context.hasRpcCall(id)) {
        cleanupAndReset();
        errorMsg = customTimeoutError;
        toast.error(`RPC call timeout on topic="${topic}", func="${func}"`, { description: errorMsg });
        callbacks?.onError?.(customTimeoutError);
      }
    }, TIMEOUT);

    // Build the payload with full type safety.
    const payload: RequestEvent = {
      t: topic,
      o: "rpc",
      f: func,
      v: args,
      i: id,
    };

    context.sendRaw(payload);
  };

  return {
    get isPending() {
      return isPending;
    },
    get errorMsg() {
      return errorMsg;
    },
    get successMsg() {
      return successMsg;
    },
    get isOpen() {
      return isOpen;
    },
    set isOpen(value) {
      isOpen = value;
    },
    call,
    reset,
    open,
  };
}

/**
 * Creates an RPC instance that automatically re-sends its last call upon WebSocket reconnection,
 * crucially waiting for its dependent topic subscription to be confirmed by the server before firing.
 *
 * It wraps the base `createRpc` by adding a reactive `$effect` that monitors both
 * the connection generation and the presence of the topic's data.
 *
 * @param topic - The WebSocket topic this RPC call depends on. Its presence in `websocketClient.topics`
 *                is used as a signal that the subscription is ready.
 * @param context - The RpcContext, typically the `websocketClient` instance.
 * @param options - Configuration options, same as `createRpc`.
 * @returns A stateful `Rpc` object with added resilience.
 */
export function createResilientRpc(topic: string, context: RpcContext, options: RpcOptions = {}): Rpc {
  const rpc = createRpc(topic, context, options);

  let lastCall: { func: string; args: any; callbacks?: CallCallbacks } | null = $state(null);
  let dataGeneration = $state(-1);

  $effect(() => {
    // 1. Read dependencies.
    const topicReady = websocketClient.topicReady[topic];
    if (!topicReady) {
      // if websocket closed, websocketClient will set all topicReady to false
      // so we can update dataGeneration to the latest connectionGeneration
      dataGeneration = untrack(() => websocketClient.connectionGeneration);
      return;
    }

    const last = untrack(() => lastCall);
    if (!last) {
      // No stored call
      return;
    }
    if (untrack(() => rpc.isPending)) {
      // last call still running
      return;
    }
    const rpcGeneration = untrack(() => dataGeneration);
    const currentGeneration = untrack(() => websocketClient.connectionGeneration);

    if (rpcGeneration > 0 && rpcGeneration < currentGeneration) {
      dataGeneration = currentGeneration;
      // console.log(
      //   `[Resilient RPC] Topic '${topic}' is ready, re-calling '${last.func}' for connection generation ${currentGeneration}`,
      // );
      rpc.call(last.func, last.args, last.callbacks);
    }
  });

  // MODIFIED: Wrap the `call` method to store the arguments and callbacks for later.
  const call = (func: string, args: any = {}, callbacks?: CallCallbacks) => {
    lastCall = { func, args, callbacks };
    dataGeneration = untrack(() => websocketClient.connectionGeneration);
    rpc.call(func, args, callbacks);
  };

  // Return an object that conforms to the Rpc interface, but with our enhanced `call` method.
  return {
    get isPending() {
      return rpc.isPending;
    },
    get errorMsg() {
      return rpc.errorMsg;
    },
    get successMsg() {
      return rpc.successMsg;
    },
    get isOpen() {
      return rpc.isOpen;
    },
    set isOpen(value) {
      rpc.isOpen = value;
    },
    reset: rpc.reset,
    open: rpc.open,
    call, // Our wrapped version.
  };
}

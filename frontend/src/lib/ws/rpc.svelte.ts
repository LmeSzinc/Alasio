import type { RequestEvent } from "./event";

/**
 * Defines the shape of the callback object used to handle operation results.
 */
export interface RpcCallbacks {
  onSuccess: (response: string) => void;
  onError: (errorMessage: string) => void;
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
  call(func: string, args: any): void;
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

  // This function is fully type-safe thanks to the `SendArgs` discriminated union.
  const call = (func: string, args: any = {}) => {
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
      },
      onError: (errMessage: string) => {
        cleanupAndReset();
        errorMsg = errMessage;
      },
    });

    // Set a timeout for the entire operation.
    operationTimeoutId = setTimeout(() => {
      if (context.hasRpcCall(id)) {
        cleanupAndReset();
        errorMsg = "RPC call timeout";
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

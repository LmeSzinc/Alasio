import { onDestroy } from "svelte";
import { websocketClient } from "./client.svelte";
import { createResilientRpc, createRpc, type RpcOptions } from "./rpc.svelte";

/**
 * A custom Svelte 5 Rune to subscribe to a WebSocket topic.
 * It automatically handles the subscription lifecycle and provides
 * a reactive data signal and an RPC handler.
 *
 * @param topic The name of the topic to subscribe to.
 * @returns A readonly object with a reactive `.data` signal and an `.rpc()` method factory.
 */
export function useTopic<T = any>(topic: string) {
  // --- Step 1: Manage Subscription Lifecycle ---
  // On creation, tell the manager we're subscribing.
  websocketClient.sub(topic);

  // On destruction, tell the manager we're unsubscribing.
  onDestroy(() => {
    websocketClient.unsub(topic);
  });

  // --- Step 2: Build the Reactive API ---
  // Create a derived signal that directly tracks the data from the manager's state.
  // This dependency is direct and clean: $derived -> websocketClient.topics[topic]
  const data = $derived(websocketClient.topics[topic] as T | undefined);

  // Create the RPC factory function for this topic.
  // It needs the topic name and the websocketClient instance (as the RpcContext).
  const rpc = (options?: RpcOptions) => createRpc(topic, websocketClient, options);
  const resilientRpc = (options?: RpcOptions) => createResilientRpc(topic, websocketClient, options);

  // --- Step 3: Return the final, user-friendly API ---
  return {
    /**
     * A reactive signal containing the data for the subscribed topic.
     * Access its value directly (e.g., `navTopic.data`).
     */
    get data() {
      return data;
    },

    /**
     * Creates a stateful RPC handler for this topic.
     * Example: `const myAction = navTopic.rpc();`
     */
    rpc,
    resilientRpc,
  };
}

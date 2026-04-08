import { onDestroy, untrack } from "svelte";
import { websocketClient, WebsocketManager } from "./client.svelte";
import { createResilientRpc, createRpc, type Rpc, type RpcOptions } from "./rpc.svelte";

export type RpcFactory = (options?: RpcOptions) => Rpc;
export type TopicLifespan<T = any> = {
  readonly data: T | undefined;
  readonly rpc: RpcFactory;
  readonly resilientRpc: RpcFactory;
};
/**
 * A custom Svelte 5 Rune to subscribe to a WebSocket topic.
 * It automatically handles the subscription lifecycle and provides
 * a reactive data signal and an RPC handler.
 *
 * @param topic The name of the topic to subscribe to.
 * @returns A readonly object with a reactive `.data` signal and an `.rpc()` method factory.
 */
export function useTopic<T = any>(topic: string, client: WebsocketManager = websocketClient): TopicLifespan<T> {
  // --- Step 1: Manage Subscription Lifecycle ---
  // Use Svelte 5 effect for subscription lifecycle.
  // It ensures sub/unsub is strictly tied to the mount status in Svelte 5.
  // CRITICAL: We MUST use untrack() here because client.sub modifies $state
  // that would otherwise cause an infinite reactivity loop.
  $effect(() => {
    untrack(() => {
      client.sub(topic);
    });
    return () => {
      untrack(() => {
        client.unsub(topic);
      });
    };
  });

  // --- Step 2: Build the Reactive API ---
  // Create a derived signal that directly tracks the data from the manager's state.
  // This dependency is direct and clean: $derived -> client.topics[topic]
  const data = $derived(client.topics[topic] as T | undefined);

  // Create the RPC factory function for this topic.
  // It needs the topic name and the client instance (as the RpcContext).
  const rpc = (options?: RpcOptions) => createRpc(topic, client, options);
  const resilientRpc = (options?: RpcOptions) => createResilientRpc(topic, client, options);

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

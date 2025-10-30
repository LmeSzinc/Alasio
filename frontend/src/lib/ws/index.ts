import { websocketClient } from "./client.svelte";
import type { Rpc, RpcOptions } from "./rpc.svelte";
import { useTopic, type RpcFactory, type TopicLifespan } from "./topic.svelte";
export { useTopic, websocketClient, type Rpc, type RpcFactory, type RpcOptions, type TopicLifespan };

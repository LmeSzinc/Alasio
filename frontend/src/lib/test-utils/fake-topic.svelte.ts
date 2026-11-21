/**
 * A stand-in for the websocket topic framework (see lib/ws/topic.svelte.ts)
 * in component tests.
 *
 * A component that reads a topic only needs two things: the data of the
 * topic, and rpc objects that record what the page asks the backend to do.
 * Driving the real client through a fake socket to deliver a snapshot adds
 * connection state (subscribe bookkeeping, reconnect, readiness) to tests that
 * are about the component, so the tests mock the module instead:
 *
 * ```ts
 * vi.mock("$lib/ws", async () => ({
 *   useTopic: (await import("$lib/test-utils/fake-topic.svelte")).useFakeTopic,
 * }));
 *
 * const topic = fakeTopic<ConfigTopicLike>("ConfigScan");
 * topic.data = { cfg_a: config("cfg_a", 1, 1) };
 * await flushEffects();
 * expect(topic.calls).toEqual([{ topic: "ConfigScan", func: "config_del", args: { name: "cfg_a" } }]);
 * ```
 *
 * The data is `$state`, so assigning it re-runs the effects of the mounted
 * components, exactly as a pushed snapshot would.
 */

/** One rpc call a component made through a fake topic. */
export type FakeRpcCall = {
  topic: string;
  func: string;
  args: any;
};

/**
 * The rpc object a fake topic hands out: the same shape as the real one, with
 * the call recorded instead of sent.
 */
export class FakeRpc {
  readonly topic: string;
  readonly calls: FakeRpcCall[];

  isOpen = $state(false);
  isPending = $state(false);
  errorMsg = $state<string | null>(null);
  successMsg = $state<string | null>(null);

  constructor(topic: string, calls: FakeRpcCall[]) {
    this.topic = topic;
    this.calls = calls;
  }

  /**
   * Records a call. The real rpc returns without sending when the connection
   * is down; a test that wants to assert "nothing was sent" simply finds no
   * entry.
   */
  call(func: string, args: any = {}): void {
    this.calls.push({ topic: this.topic, func, args });
  }

  reset(): void {
    // Like the real rpc: the dialog visibility is not part of the reset, the
    // dialogs call reset() on open (see DialogAdd's resetForm), so closing
    // here would close the dialog the user just opened.
    this.isPending = false;
    this.errorMsg = null;
    this.successMsg = null;
  }
  open(): void {
    this.reset();
    this.isOpen = true;
  }
}

/** A topic of a fake client: its data and every rpc call made on it. */
export class FakeTopic<T = any> {
  readonly name: string;
  readonly calls: FakeRpcCall[] = [];
  /** Every rpc object handed out, in creation order (the page keeps several, one per dialog). */
  readonly rpcs: FakeRpc[] = [];
  data = $state<T | undefined>(undefined);

  constructor(name: string) {
    this.name = name;
  }

  rpc(): FakeRpc {
    const rpc = new FakeRpc(this.name, this.calls);
    this.rpcs.push(rpc);
    return rpc;
  }

  // The real factory also exposes a resilient variant; no page under test
  // depends on the replay of a call after a reconnect.
  resilientRpc(): FakeRpc {
    return this.rpc();
  }
}

const topics = new Map<string, FakeTopic<any>>();

/**
 * The topic of a name, created on first use.
 *
 * Args:
 *   name: The topic name, e.g. `ConfigScan`
 *
 * Returns:
 *   FakeTopic: its data and the rpc calls made on it
 */
export function fakeTopic<T = any>(name: string): FakeTopic<T> {
  let topic = topics.get(name);
  if (!topic) {
    topic = new FakeTopic(name);
    topics.set(name, topic);
  }
  return topic as FakeTopic<T>;
}

/**
 * The `useTopic` of the mocked `$lib/ws`.
 *
 * Args:
 *   name: The topic the component subscribes to
 *
 * Returns:
 *   FakeTopic: the shared fake of that name
 */
export function useFakeTopic<T = any>(name: string): FakeTopic<T> {
  return fakeTopic<T>(name);
}

/** Forgets every topic and the calls they recorded. Call it in beforeEach. */
export function resetFakeTopics(): void {
  topics.clear();
}

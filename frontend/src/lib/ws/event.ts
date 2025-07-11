// --- Type Definitions matching the Python backend ---

// For RequestEvent and ResponseEvent, omit_defaults=True implies that
// fields with default values are optional in the serialized/deserialized form.
// In TypeScript, this is represented by making them optional properties (`?`).

/**
 * RequestEvent represents a client-to-server request for topic subscription, unsubscription, or RPC calls.
 */
export interface RequestEvent {
  // Topic, topic name.
  t: string;
  // Operation.
  // operation can be omitted, if so, operation is considered to be "sub"
  // if operation is "sub", operation should be omitted
  o?: "sub" | "unsub" | "rpc";
  // Function, RPC function Name.
  // if operation is "sub" or "unsub", "f" should be omitted
  f?: string;
  // Value, RPC function argument value.
  // if operation is "sub" or "unsub", "v" should be omitted
  // value can be omitted, if so, value is consider to be empty dict {}
  v?: any; // Corresponds to Python's Any and default_factory=dict
  // ID, RPC event ID, a random unique ID to track RPC calls.
  // if operation is "sub" or "unsub", "i" should be omitted
  // A ResponseEvent with the same ID will be sent when the RPC event is finished
  i?: string;
}

/**
 * ResponseEvent represents a server-to-client event, including topic updates and RPC responses.
 */
export interface ResponseEvent {
  // Topic.
  t: string;
  // Operation.
  // operation may be omitted, if so, operation is "add"
  o?: "full" | "add" | "set" | "del";
  // Keys.
  // keys may be omitted, if so, keys is (), meaning doing operation at data root
  k?: Array<string | number>; // Corresponds to Python's Tuple[Union[str, int], ...]
  // Value.
  // value may be omitted, if so, value is None
  // if operation is "del", value will be omitted
  v?: any | null; // Corresponds to Python's Any and default=None
  // ID, RPC event ID, a random unique ID to track RPC calls.
  // If present, this event is a response to an RPC call.
  // RPC event ID only comes with topic and value.
  // - If event success, value is omitted.
  // - If event failed, value is a string of error message.
  i?: string;
}

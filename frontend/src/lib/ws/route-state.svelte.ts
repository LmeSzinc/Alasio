/**
 * Route-group state shared between the layouts and the websocket client.
 *
 * `.public` is true while any (public) route is mounted, and every ws
 * client (the main client and the preview client, which extends it) reads
 * it as "refuse to connect": an unauthenticated handshake is accepted
 * then closed(4001) by the backend (a refused handshake would only
 * surface as 1006 to the browser), so connecting there is a guaranteed
 * failure. The (public) layout sets the flag in its load (which runs
 * before any component is created) and clears it on destroy — a private
 * page mounting its components subscribes after that destroy hook, and
 * their connect() calls pass the guard.
 *
 * A rejected session (server close 4001) raises the same flag before that
 * navigation lands: the login page is the only place left for such a
 * session, so every ws client must stop connecting and stop replaying
 * its rpcs (the default subscription is marked ready the moment a
 * connection opens, which is the exact signal resilient rpcs wait for)
 * until the login page is reached. Without it the page would revive the
 * connection forever — connect -> 4001 -> goto -> replay -> connect —
 * cancelling the navigation over and over and leaving the emptied
 * private view on screen, buried under a pile of rpc timeout toasts.
 * The (public) layout clears the flag again when that session ends.
 */
export const routeState = $state({ public: false });

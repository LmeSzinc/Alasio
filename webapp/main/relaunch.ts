/**
 * Relaunch request parsing.
 *
 * The backend asks for a whole-application relaunch when the electron archive
 * of the package has to replace the installed one: the updater is already
 * waiting for the chain to go down, and the supervisor prints the request to
 * stdout as `[Supervisor] relaunch:<token>`.
 *
 * Every worker of the backend can write to that same stdout, so the line is
 * accepted only when it carries the token Electron currently accepts — the
 * same chain rule the token announcements use. A forged line can match the
 * format, but the forger does not know the token: it only lives in the
 * supervisor / backend / Electron memory.
 */

export const RELAUNCH_RE = /^\[Supervisor\] relaunch:([0-9a-f]{64})$/;

/**
 * Parse one stdout line into a relaunch request.
 *
 * Args:
 *     line (string): One complete line without the trailing newline
 *     token (string | null): The token currently accepted, null before the
 *         first announcement
 *
 * Returns:
 *     boolean: True when the line is a relaunch request of the current chain
 */
export function parseRelaunch(line: string, token: string | null): boolean {
  if (!token) return false;
  const match = RELAUNCH_RE.exec(line);
  if (!match) return false;
  return match[1] === token;
}

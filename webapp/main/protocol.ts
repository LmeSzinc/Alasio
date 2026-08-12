import { app, protocol, net } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { pathToFileURL } from 'url';

// Electron 25+ only: protocol.handle() with a fetch-style handler.
type HandleProtocol = (
  scheme: string,
  handler: (request: Request) => Promise<Response> | Response
) => void;
// Electron <= 24: registerFileProtocol() with a callback.
type LegacyRegisterProtocol = (
  scheme: string,
  handler: (request: any, callback: (result: { path?: string; error?: number }) => void) => void
) => void;

// The electron d.ts version installed defines only one of the two APIs,
// so cast through any to keep both electron 22 and modern versions compiling.
const protocolHandle = (protocol as any).handle as HandleProtocol | undefined;
const legacyRegister = (protocol as any).registerFileProtocol as
  | LegacyRegisterProtocol
  | undefined;
// net.fetch is Electron 25+ only, same compatibility treatment
const netFetch = (net as any).fetch as ((url: string) => Promise<Response>) | undefined;

function resolveSafe(rendererDir: string, urlPath: string): string {
  const filePath = path.normalize(path.join(rendererDir, decodeURIComponent(urlPath)));
  if (!filePath.startsWith(rendererDir + path.sep)) {
    throw new Error(`Blocked path outside renderer dir: ${filePath}`);
  }
  return filePath;
}

// Map a URL path to a file, resolving directory requests (e.g. app://bundle/ -> index.html)
function resolveFile(rendererDir: string, urlPath: string): string {
  const filePath = resolveSafe(rendererDir, urlPath);
  try {
    if (fs.statSync(filePath).isDirectory()) {
      return path.join(filePath, 'index.html');
    }
  } catch (e) {
    // File not found: let the request fail with the original path
  }
  return filePath;
}

/**
 * Register the app:// custom protocol serving the renderer build output.
 * Must be called before the app is ready (registerSchemesAsPrivileged requirement).
 *
 * Args:
 *     rendererDir (str): Absolute path of the built renderer directory
 */
export function registerAppProtocol(rendererDir: string) {
  // standard + secure make app:// URLs parse like http(s), so
  // history.pushState works for the SvelteKit client-side router.
  protocol.registerSchemesAsPrivileged([
    { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ]);

  app.whenReady().then(() => {
    if (protocolHandle && netFetch) {
      protocolHandle('app', (request) => {
        const filePath = resolveFile(rendererDir, new URL(request.url).pathname);
        return netFetch(pathToFileURL(filePath).toString());
      });
    } else if (legacyRegister) {
      legacyRegister('app', (request, callback) => {
        const filePath = resolveFile(rendererDir, new URL(request.url).pathname);
        callback({ path: filePath });
      });
    }
  });
}

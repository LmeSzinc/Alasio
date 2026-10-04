/**
 * Atomic file write and read for the frontend / webapp scripts.
 *
 * A port of `alasio/ext/path/atomic.py`: a write goes to a temporary file next
 * to the target and replaces it with a rename, which is atomic on every OS, so
 * a concurrent reader sees either the old or the new content, never a half
 * written file. On Windows a rename fails while another process holds the
 * target, so the replace and the reads retry with an exponential backoff.
 *
 * The scripts run concurrently with each other and with the dev server: the
 * i18n JSON of the frontend is maintained by the vite plugin, `pnpm run
 * i18ngen`, `pnpm run codegen` and the build init of `svelte-kit sync` at the
 * same time, so every shared file must be written and read through here.
 */
import { promises as fs } from "node:fs";
import { dirname } from "node:path";

// Max attempts if another process is reading/writing, effective only on Windows
const WINDOWS_MAX_ATTEMPT = 8;
// Base time to wait between retries (milliseconds)
const WINDOWS_RETRY_DELAY = 50;
/**
 * Errors a rename raises on Windows while another process holds the target:
 * EPERM / EACCES (Python's PermissionError) and EBUSY (sharing violation)
 */
const WINDOWS_LOCK_ERRORS = new Set(["EPERM", "EACCES", "EBUSY"]);

const IS_WINDOWS = process.platform === "win32";

const ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/**
 * Returns a random ID of 6 alphanumeric characters.
 *
 * Returns:
 *     str: Random ID, like "sTD2kF"
 */
export function randomId(): string {
  let id = "";
  for (let i = 0; i < 6; i++) {
    id += ID_CHARS[Math.floor(Math.random() * ID_CHARS.length)];
  }
  return id;
}

/**
 * Check if a file name is a temporary file.
 *
 * Args:
 *     file (str): File name to check, like "Home.json.sTD2kF.tmp"
 *
 * Returns:
 *     bool: True if file is a temporary file
 */
export function isTmpFile(file: string): boolean {
  // Check suffix first to reduce regex calls
  if (!file.endsWith(".tmp")) return false;
  // Check temp file format
  if (file.slice(-11, -10) !== ".") return false;
  const id = file.slice(-10, -4);
  return id.length === 6 && /^[a-zA-Z0-9]{6}$/.test(id);
}

/**
 * Convert a file path to its temporary path.
 * Home.json -> Home.json.sTD2kF.tmp
 *
 * Args:
 *     file (str): Original file path
 *
 * Returns:
 *     str: Temporary file path
 */
export function toTmpFile(file: string): string {
  return `${file}.${randomId()}.tmp`;
}

/**
 * Convert a temporary file path back to the original path.
 * Home.json.sTD2kF.tmp -> Home.json
 *
 * Args:
 *     file (str): Temporary file path
 *
 * Returns:
 *     str: Original file path
 */
export function toNonTmpFile(file: string): string {
  return isTmpFile(file) ? file.slice(0, -11) : file;
}

/**
 * Exponential backoff for a locked file on Windows.
 *
 * Args:
 *     attempt (int): Current attempt, starting from 0
 *
 * Returns:
 *     int: Milliseconds to wait, 1s at most
 */
export function windowsAttemptDelay(attempt: number): number {
  // A large attempt causes a heavy power calculation
  const clamped = Math.min(Math.max(attempt, 0), 10);
  return Math.min(2 ** clamped * WINDOWS_RETRY_DELAY, 1000);
}

function errorCode(error: unknown): string {
  return (error as NodeJS.ErrnoException | null)?.code ?? "";
}

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Run an operation, retrying while the target is locked by another process
 * (Windows only, Linux and Mac allow reading/writing while replacing).
 *
 * Args:
 *     operation (function): Operation to run, may raise a lock error
 *
 * Returns:
 *     any: Result of the operation
 */
async function retryOnLock<T>(operation: () => Promise<T>): Promise<T> {
  if (!IS_WINDOWS) return operation();
  let lastError: unknown;
  for (let attempt = 0; attempt < WINDOWS_MAX_ATTEMPT; attempt++) {
    try {
      return await operation();
    } catch (error) {
      if (!WINDOWS_LOCK_ERRORS.has(errorCode(error))) throw error;
      lastError = error;
      await sleep(windowsAttemptDelay(attempt));
    }
  }
  throw lastError;
}

async function removeQuietly(file: string): Promise<void> {
  try {
    await fs.unlink(file);
  } catch {
    // The temp file is already gone, or still locked: nothing to clean up
  }
}

/**
 * Replace a temporary file over the target, atomically.
 *
 * Rename replaces the target on every OS; on Windows it fails while another
 * process is reading the target, so it retries with an exponential backoff.
 * The temp file is removed when the replace fails for good, so a failed write
 * does not leave a temp file behind.
 *
 * Args:
 *     tmp (str): Temporary file path
 *     file (str): Target file path
 *
 * Raises:
 *     Error: Last error, if the replace did not succeed
 *     Error: ENOENT if the temp file gets deleted unexpectedly
 */
export async function replaceTmp(tmp: string, file: string): Promise<void> {
  let lastError: unknown = null;
  const maxAttempt = IS_WINDOWS ? WINDOWS_MAX_ATTEMPT : 1;
  for (let attempt = 0; attempt < maxAttempt; attempt++) {
    try {
      // Atomic operation
      await fs.rename(tmp, file);
      // success
      return;
    } catch (error) {
      if (errorCode(error) === "ENOENT") {
        // The temp file gets deleted unexpectedly, nothing to replace with
        throw error;
      }
      if (!IS_WINDOWS || !WINDOWS_LOCK_ERRORS.has(errorCode(error))) {
        lastError = error;
        break;
      }
      lastError = error;
      // Another process is still reading the target
      await sleep(windowsAttemptDelay(attempt));
    }
  }
  // Clean up temp file on failure
  await removeQuietly(tmp);
  throw lastError ?? new Error(`Failed to replace "${file}"`);
}

/**
 * Rename a file or directory, retrying while the file is locked by another
 * process (Windows only). Port of `atomic_rename` in
 * `alasio/ext/path/atomic.py`.
 *
 * Unlike replaceTmp this does not replace an existing target: an
 * EEXIST / ENOTEMPTY is a real failure and is raised immediately.
 *
 * Args:
 *     from (str): Source file/directory path
 *     to (str): Target file/directory path
 */
export async function atomicRename(from: string, to: string): Promise<void> {
  await retryOnLock(async () => {
    // Atomic operation
    await fs.rename(from, to);
  });
}

/**
 * Write a file, creating its parent directory when missing, and flush the
 * data to disk before returning.
 *
 * This is the plain write (a port of `file_write` in
 * `alasio/ext/path/atomic.py`): the target is truncated before the new
 * content is written, so use atomicWrite when another process may read the
 * file while it is written.
 *
 * Args:
 *     file (str): Target file path
 *     data (str | Uint8Array): Data to write
 */
export async function fileWrite(file: string, data: string | Uint8Array): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    try {
      const handle = await fs.open(file, "w");
      try {
        await handle.writeFile(data);
        // Ensure data is flushed to disk, so a crash can not leave the file
        // with its content still in the OS cache
        await handle.sync();
      } finally {
        await handle.close();
      }
      return;
    } catch (error) {
      // The parent directory may be missing: create it once and write again
      if (attempt > 0 || errorCode(error) !== "ENOENT") throw error;
      const directory = dirname(file);
      if (directory) {
        await fs.mkdir(directory, { recursive: true });
      }
    }
  }
}

/**
 * Atomic file write with minimal IO operation.
 *
 * The data is written to a temporary file next to the target (auto creating
 * the parent directory) and then replaces the target, so a reader can never
 * observe a half written file. Reads of a file being replaced are retried in
 * atomicReadText / atomicReadBytes.
 *
 * Args:
 *     file (str): Target file path
 *     data (str | Uint8Array): Data to write
 */
export async function atomicWrite(file: string, data: string | Uint8Array): Promise<void> {
  const tmp = toTmpFile(file);
  await fileWrite(tmp, data);
  await replaceTmp(tmp, file);
}

/**
 * Remove a file, retrying while another process is reading/writing it
 * (Windows only). Port of `atomic_remove` in `alasio/ext/path/atomic.py`.
 *
 * Args:
 *     file (str): File path to remove
 *
 * Returns:
 *     bool: True if a file was removed, False if it did not exist
 */
export async function atomicRemove(file: string): Promise<boolean> {
  return await retryOnLock(async () => {
    try {
      await fs.unlink(file);
      return true;
    } catch (error) {
      if (errorCode(error) === "ENOENT") {
        // If file not exist, just no need to remove
        return false;
      }
      throw error;
    }
  });
}

/**
 * Read a text file, retrying while another process is replacing it.
 *
 * Args:
 *     file (str): Source file path
 *     encoding (str): Text encoding. Defaults to 'utf-8'.
 *
 * Returns:
 *     str: File content
 */
export async function atomicReadText(file: string, encoding: BufferEncoding = "utf-8"): Promise<string> {
  return await retryOnLock(() => fs.readFile(file, { encoding }));
}

/**
 * Read a binary file, retrying while another process is replacing it.
 *
 * Args:
 *     file (str): Source file path
 *
 * Returns:
 *     Uint8Array: File content
 */
export async function atomicReadBytes(file: string): Promise<Uint8Array> {
  return await retryOnLock(() => fs.readFile(file));
}

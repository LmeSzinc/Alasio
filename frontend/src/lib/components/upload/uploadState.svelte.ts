import type { Rpc } from "$lib/ws";
import { untrack } from "svelte";

interface UploadItem {
  id: string;
  file: File;
  status: "pending" | "uploading" | "success" | "error";
  error?: string;
  rpc?: Rpc;
}

export type OnUploadFunction = (file: File) => Rpc;

/**
 * UploadState manages the state of file uploads.
 * Each instance can have its own upload handler and track its own uploads.
 * Not a singleton - can be instantiated per page/component.
 */
export class UploadState {
  queue = $state<UploadItem[]>([]);
  private onUploadCallback: OnUploadFunction;

  constructor(onUpload: OnUploadFunction) {
    this.onUploadCallback = onUpload;

    /**
     * Watch all RPC states and update queue accordingly
     * This runs in a $effect, so it's reactive to RPC state changes
     */
    $effect(() => {
      // Process each upload item with an rpc
      for (let i = 0; i < this.queue.length; i++) {
        const item = this.queue[i];

        if (!item.rpc) continue;

        // Check for success
        if (item.rpc.successMsg) {
          untrack(() => {
            this.markSuccess(item.id);
          });
        }
        // Check for error
        else if (item.rpc.errorMsg) {
          untrack(() => {
            this.markError(item.id, item.rpc!.errorMsg || "Error");
          });
        }
      }
    });
  }

  /**
   * Add files to the upload queue and start uploading
   */
  addFiles(files: File[] | FileList): void {
    const filesArray = Array.from(files);

    for (const file of filesArray) {
      const id = `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;

      try {
        const rpc = this.onUploadCallback(file);
        // Add to queue with rpc
        this.queue.push({
          id,
          file,
          status: "uploading",
          rpc,
        });
      } catch (error) {
        // Add to queue without rpc if failed to create
        this.queue.push({
          id,
          file,
          status: "error",
          error: error instanceof Error ? error.message : "Failed to start upload",
        });
      }
    }
  }

  /**
   * Mark an upload as successful
   */
  private markSuccess(id: string): void {
    const index = this.queue.findIndex((u) => u.id === id);
    if (index !== -1) {
      this.queue[index].status = "success";
      this.queue[index].rpc = undefined;
      this.checkAutoClear();
    }
  }

  /**
   * Mark an upload as failed
   */
  private markError(id: string, error: string): void {
    const index = this.queue.findIndex((u) => u.id === id);
    if (index !== -1) {
      this.queue[index].status = "error";
      this.queue[index].error = error;
      this.queue[index].rpc = undefined;
    }
  }

  /**
   * Check if all uploads are complete and auto-clear if no errors
   */
  private checkAutoClear(): void {
    if (this.allComplete && this.errorCount === 0) {
      setTimeout(() => {
        // Double check in case state changed during timeout
        if (this.allComplete && this.errorCount === 0) {
          this.clearSuccessful();
        }
      }, 3000);
    }
  }

  /**
   * Retry a failed upload
   */
  private retryUpload(id: string): void {
    const index = this.queue.findIndex((u) => u.id === id);
    if (index === -1) return;

    const upload = this.queue[index];

    this.queue[index].status = "uploading";
    this.queue[index].error = undefined;

    try {
      const rpc = this.onUploadCallback(upload.file);
      this.queue[index].rpc = rpc;
    } catch (error) {
      this.markError(id, error instanceof Error ? error.message : "Failed to retry upload");
    }
  }

  /**
   * Retry all failed uploads
   */
  retryFailed(): void {
    const failedIds = this.queue.filter((u) => u.status === "error").map((u) => u.id);

    for (const id of failedIds) {
      this.retryUpload(id);
    }
  }

  /**
   * Clear successful uploads from the queue
   */
  clearSuccessful(): void {
    this.queue = this.queue.filter((item) => item.status !== "success");
  }

  /**
   * Clear all completed uploads (success or error)
   */
  clearAll(): void {
    this.queue = this.queue.filter((item) => item.status === "uploading" || item.status === "pending");
  }

  /**
   * Clear the entire queue
   */
  clear(): void {
    this.queue = [];
  }

  // Derived states
  get pendingCount(): number {
    return this.queue.filter((u) => u.status === "pending").length;
  }

  get uploadingCount(): number {
    return this.queue.filter((u) => u.status === "uploading").length;
  }

  get successCount(): number {
    return this.queue.filter((u) => u.status === "success").length;
  }

  get errorCount(): number {
    return this.queue.filter((u) => u.status === "error").length;
  }

  get allComplete(): boolean {
    return this.pendingCount === 0 && this.uploadingCount === 0;
  }

  get isVisible(): boolean {
    return this.queue.length > 0;
  }
}

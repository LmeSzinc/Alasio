/**
 * Tests for the update toast module (updateToast.ts)
 *
 * The module hands the toast over to svelte-sonner: the library is mocked so
 * the calls (the fresh id of every toast, the dismiss-before-create
 * replacement) are asserted instead of rendering toasts.
 */
import { type ToastT, toast } from "svelte-sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UpdateToastContent from "./UpdateToastContent.svelte";
import { UPDATE_TOAST_ID_PREFIX, dismissUpdateToast, showUpdateToast } from "./updateToast";

vi.mock("svelte-sonner", () => ({
  toast: { custom: vi.fn(), dismiss: vi.fn(), getActiveToasts: vi.fn(() => []) },
}));

/** One entry of toast.getActiveToasts(), as much as the module reads of it. */
function liveToast(id: string | number): ToastT {
  return { id, type: "default" };
}

beforeEach(() => {
  vi.mocked(toast.custom).mockClear();
  vi.mocked(toast.dismiss).mockClear();
  vi.mocked(toast.getActiveToasts).mockClear();
  vi.mocked(toast.getActiveToasts).mockReturnValue([]);
});

describe("showUpdateToast", () => {
  it("creates a persistent toast carrying the updates", () => {
    showUpdateToast([{ mod: "alasio", latest: "0beb992a" }]);

    expect(toast.custom).toHaveBeenCalledTimes(1);
    const [component, options] = vi.mocked(toast.custom).mock.calls[0];
    expect(component).toBe(UpdateToastContent);
    expect(options!.id).toMatch(new RegExp(`^${UPDATE_TOAST_ID_PREFIX}-\\d+$`));
    expect(options!.duration).toBe(Number.POSITIVE_INFINITY);
    expect(options!.componentProps).toEqual({ updates: [{ mod: "alasio", latest: "0beb992a" }] });
  });

  it("dismisses the toast on screen and creates the replacement under a fresh id", () => {
    vi.mocked(toast.getActiveToasts).mockReturnValue([liveToast("update-available-7")]);

    showUpdateToast([
      { mod: "alasio", latest: "0beb992a" },
      { mod: "ExampleMod", latest: "d3599d6a" },
    ]);

    // The toast on screen is dismissed under its own id (its exit animation
    // plays); the replacement is a new toast, never a merge into the old id
    expect(toast.dismiss).toHaveBeenCalledTimes(1);
    expect(toast.dismiss).toHaveBeenCalledWith("update-available-7");
    const [, options] = vi.mocked(toast.custom).mock.calls[0];
    expect(options!.id).not.toBe("update-available-7");
    expect(options!.componentProps).toEqual({
      updates: [
        { mod: "alasio", latest: "0beb992a" },
        { mod: "ExampleMod", latest: "d3599d6a" },
      ],
    });
  });

  it("leaves the toasts that are not the update toast alone", () => {
    vi.mocked(toast.getActiveToasts).mockReturnValue([liveToast(1), liveToast("update-available-2")]);

    showUpdateToast([{ mod: "alasio", latest: "0beb992a" }]);

    expect(toast.dismiss).toHaveBeenCalledTimes(1);
    expect(toast.dismiss).toHaveBeenCalledWith("update-available-2");
  });

  it("is a no-op for an empty list", () => {
    showUpdateToast([]);

    expect(toast.custom).not.toHaveBeenCalled();
    expect(toast.dismiss).not.toHaveBeenCalled();
  });
});

describe("dismissUpdateToast", () => {
  it("dismisses every update toast on screen, nothing else", () => {
    vi.mocked(toast.getActiveToasts).mockReturnValue([
      liveToast("update-available-1"),
      liveToast(3),
      liveToast("update-available-2"),
    ]);

    dismissUpdateToast();

    expect(toast.dismiss).toHaveBeenCalledTimes(2);
    expect(toast.dismiss).toHaveBeenCalledWith("update-available-1");
    expect(toast.dismiss).toHaveBeenCalledWith("update-available-2");
  });

  it("is a no-op when no update toast is on screen", () => {
    dismissUpdateToast();

    expect(toast.dismiss).not.toHaveBeenCalled();
  });
});

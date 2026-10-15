/**
 * Tests for the helpers of the update flow ($lib/components/update/utils).
 */
import { describe, expect, it } from "vitest";
import { prettyVersion } from "./utils";

describe("TestPrettyVersion", () => {
  it("shortens the sha1 of a pack to its first 8 characters", () => {
    expect(prettyVersion("0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8")).toBe("0beb992a");
    expect(prettyVersion("ab41c81d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b")).toBe("ab41c81d");
  });

  it("displays every other version as it is", () => {
    // a tag, the version of a wheel, a branch name
    expect(prettyVersion("v1.2.3")).toBe("v1.2.3");
    expect(prettyVersion("0.9.2")).toBe("0.9.2");
    expect(prettyVersion("main")).toBe("main");
  });

  it("keeps a version of the wrong length or alphabet whole", () => {
    // 39 and 41 characters, an uppercase sha1 (a pack version is lowercase hex)
    expect(prettyVersion("0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c")).toBe("0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c");
    expect(prettyVersion("0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8d")).toBe(
      "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8d",
    );
    expect(prettyVersion("0BEB992A4B31F0E2F8B2F3B7A1C2D3E4F5A6B7C8")).toBe("0BEB992A4B31F0E2F8B2F3B7A1C2D3E4F5A6B7C8");
    expect(prettyVersion("z".repeat(40))).toBe("z".repeat(40));
  });

  it("returns an empty version as it is", () => {
    expect(prettyVersion("")).toBe("");
  });
});

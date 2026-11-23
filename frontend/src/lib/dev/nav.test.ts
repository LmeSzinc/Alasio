/**
 * Tests for the dev navigation model.
 *
 * The module is the single source of truth for two things that must not drift
 * apart: the list the sidebar renders, and the layout policy that decides which
 * pages run without the navigation column. Only the pages that are a workspace
 * of their own drop the column, so the cases here check the routes against that
 * policy rather than the translated labels.
 */
import { describe, expect, it } from "vitest";
import { devNavGroups, devNavTitle, isCompactPath, isNavPath, navColumnClass } from "$lib/dev/nav";

describe("TestIsNavPath", () => {
  it.each([
    ["/dev/debug", "/dev/debug", true],
    ["/dev/debug/ws", "/dev/debug", true],
    ["/dev/debugger", "/dev/debug", false],
    ["/dev/assets", "/dev/assets", true],
    ["/dev/assets/", "/dev/assets", true],
    ["/dev/config", "/dev/assets", false],
    ["/dev", "/dev", true],
  ])("%s against %s is %s", (pathname, base, expected) => {
    expect(isNavPath(pathname, base)).toBe(expected);
  });
});

describe("TestIsCompactPath", () => {
  it.each(["/dev/assets", "/dev/golden-test"])("drops the nav column on %s", (pathname) => {
    expect(isCompactPath(pathname)).toBe(true);
  });

  it.each([
    "/dev/config",
    "/dev/setting",
    "/dev/updater",
    // Only the two workspaces drop the column: the other dev pages are
    // ordinary pages, and the navigation is what makes a series of them cheap
    // to visit
    "/dev/compat",
    "/dev/debug/ws",
    "/dev/debug/log",
    "/config/example/overview",
    "/main",
  ])("keeps the nav column on %s", (pathname) => {
    expect(isCompactPath(pathname)).toBe(false);
  });

  it("keeps the nav column on every dev page that is not a workspace", () => {
    const tools = devNavGroups()
      .flatMap((group) => group.items)
      .filter((item) => !["/dev/assets", "/dev/golden-test"].includes(item.path));

    expect(tools.length).toBeGreaterThan(0);
    for (const item of tools) {
      expect(isCompactPath(item.path)).toBe(false);
    }
  });

  it("treats a page below a listed route as compact too", () => {
    expect(isCompactPath("/dev/assets/combat")).toBe(true);
  });
});

describe("TestDevNavGroups", () => {
  it("lists the sections in display order with unique ids", () => {
    const groups = devNavGroups();

    expect(groups.map((group) => group.id)).toEqual(["alasio", "dev", "debug"]);
    expect(new Set(groups.map((group) => group.id)).size).toBe(groups.length);
  });

  it("marks exactly one section as the sidebar header", () => {
    // The lead section is drawn with the large header row, the others with a
    // small title; the first section is the only one.
    expect(
      devNavGroups()
        .filter((group) => group.lead)
        .map((group) => group.id),
    ).toEqual(["alasio"]);
  });

  it("gives every entry a unique route under /dev", () => {
    const paths = devNavGroups().flatMap((group) => group.items.map((item) => item.path));

    expect(new Set(paths).size).toBe(paths.length);
    for (const path of paths) {
      expect(path.startsWith("/dev/")).toBe(true);
    }
  });

  it("keeps the user-facing tools in the lead section", () => {
    const lead = devNavGroups().find((group) => group.lead);
    if (!lead) throw new Error("the lead group must exist");

    expect(lead.items.map((item) => item.path)).toEqual(["/dev/config", "/dev/setting", "/dev/updater"]);
  });
});

describe("TestNavColumnClass", () => {
  it.each([
    ["/dev/config", false, "w-50"],
    ["/dev/config", true, "w-60"],
    ["/dev/setting", false, "w-50"],
    ["/dev/updater", true, "w-60"],
    ["/config/example/overview", false, "w-50"],
  ])("keeps the column on %s (xl: %s) as %s", (pathname, isXL, expected) => {
    expect(navColumnClass(pathname, isXL)).toBe(expected);
  });

  it.each(["/dev/assets", "/dev/golden-test"])("hides the column on %s at the medium breakpoint", (pathname) => {
    // `hidden` is the class that stops the column from being laid out at all.
    expect(navColumnClass(pathname, false)).toBe("hidden");
  });

  it.each(["/dev/compat", "/dev/debug/ws", "/dev/config"])(
    "keeps the column on %s at the medium breakpoint",
    (pathname) => {
      expect(navColumnClass(pathname, false)).toBe("w-50");
    },
  );

  it.each(["/dev/assets", "/dev/golden-test"])("keeps the column on %s at the large breakpoint", (pathname) => {
    // A large window has room for both: hiding the navigation there would cost
    // a click on every tool switch for no width gain.
    expect(navColumnClass(pathname, true)).toBe("w-60");
  });
});

describe("TestDevNavTitle", () => {
  it("returns the label of the owning entry", () => {
    const assets = devNavGroups()
      .flatMap((group) => group.items)
      .find((item) => item.path === "/dev/assets");
    if (!assets) throw new Error("the asset manager must be listed");

    expect(devNavTitle("/dev/assets")).toBe(assets.name);
    expect(devNavTitle("/dev/assets/combat")).toBe(assets.name);
  });

  it("falls back to the pathname for an unlisted route", () => {
    expect(devNavTitle("/dev/nothing-here")).toBe("/dev/nothing-here");
  });
});

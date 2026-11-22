import { dev } from "$app/env";
import { t } from "$lib/i18n";

/**
 * One entry of the dev navigation: the route and its label.
 */
export interface DevNavItem {
  path: string;
  name: string;
}

/**
 * A titled section of the dev navigation.
 *
 * The section id is also a layout policy: sections listed in
 * `COMPACT_GROUPS` are developer pages that run without the navigation column,
 * so their content gets the whole width next to the config column.
 */
export interface DevNavGroup {
  id: "alasio" | "dev" | "debug";
  title: string;
  items: DevNavItem[];
  /** Rendered as the sidebar header rather than a small section title */
  lead: boolean;
}

/** Sections whose pages drop the navigation column */
const COMPACT_GROUPS: ReadonlySet<DevNavGroup["id"]> = new Set<DevNavGroup["id"]>(["dev", "debug"]);

/**
 * Sections of the dev navigation, in display order.
 *
 * Read this inside a `$derived`: the labels come from the i18n state, so the
 * list follows a language switch.
 *
 * Returns:
 *   DevNavGroup[]: the sections, `Alasio Tools` first
 */
export function devNavGroups(): DevNavGroup[] {
  return [
    {
      id: "alasio",
      title: t.DevTool.AlasioTool(),
      lead: true,
      items: [
        { path: "/dev/config", name: t.ConfigScan.ConfigManager() },
        { path: "/dev/setting", name: t.DevTool.AlasioSettings() },
        { path: "/dev/updater", name: t.Mod.Updater() },
      ],
    },
    {
      id: "dev",
      title: t.DevTool.DevTool(),
      lead: false,
      items: [
        { path: "/dev/assets", name: t.AssetManager.AssetManager() },
        { path: "/dev/golden-test", name: t.GoldenTest.Title() },
        { path: "/dev/compat", name: "Browser Compatibility" },
      ],
    },
    {
      id: "debug",
      title: t.DevTool.DebugTool(),
      lead: false,
      // /dev/debug/* routes are dropped from the production build by the
      // svelte-drop-dev-page plugin, so they are only listed in development
      items: dev
        ? [
            { path: "/dev/debug/goldentest", name: "Golden Test Demo" },
            { path: "/dev/debug/ws", name: t.WebsocketTest.Title() },
            { path: "/dev/debug/modmanager", name: "Mod Manager" },
            { path: "/dev/debug/updatetoast", name: "Update Toast" },
            { path: "/dev/debug/workerstatus", name: "Worker Status" },
            { path: "/dev/debug/aside", name: "Config Aside" },
            { path: "/dev/debug/restartstatus", name: "Restart Status" },
            { path: "/dev/debug/scheduler", name: "Scheduler" },
            { path: "/dev/debug/preview", name: "Preview" },
            { path: "/dev/debug/dashboard", name: "Dashboard" },
            { path: "/dev/debug/dashboardgroup", name: "Dashboard Group" },
            { path: "/dev/debug/log", name: "Log Viewer" },
            { path: "/dev/debug/configdisplay", name: "Config Display" },
            { path: "/dev/debug/arginput", name: "Arg Input" },
          ]
        : [],
    },
  ];
}

/**
 * Whether a route is `base` itself or a page below it.
 *
 * Args:
 *   pathname: Current route, e.g. `/dev/debug/ws`
 *   base: Route of a navigation entry, e.g. `/dev/debug`
 *
 * Returns:
 *   bool: true for `/dev/debug` and `/dev/debug/ws`, false for `/dev/debugger`
 */
export function isNavPath(pathname: string, base: string): boolean {
  return pathname === base || pathname.startsWith(`${base}/`);
}

/**
 * Whether the page runs without the dev navigation column.
 *
 * These pages are a full-window workspace of their own (a 2×2 grid, a file tree
 * with source and screenshots), so the navigation column is dropped and only
 * the config column stays.
 *
 * Args:
 *   pathname: Current route
 *
 * Returns:
 *   bool: true for `/dev/assets`, `/dev/golden-test`, `/dev/compat` and `/dev/debug/*`
 */
export function isCompactPath(pathname: string): boolean {
  return devNavGroups().some(
    (group) => COMPACT_GROUPS.has(group.id) && group.items.some((item) => isNavPath(pathname, item.path)),
  );
}

/**
 * Label of the navigation entry that owns a route.
 *
 * Args:
 *   pathname: Current route
 *
 * Returns:
 *   str: the entry label, or the pathname itself when no entry matches
 */
export function devNavTitle(pathname: string): string {
  for (const group of devNavGroups()) {
    for (const item of group.items) {
      if (isNavPath(pathname, item.path)) return item.name;
    }
  }
  return pathname;
}

/**
 * Width classes of the navigation column of a private page.
 *
 * On medium screens a compact dev page (the asset manager, the golden test
 * review, the debug pages) hides the column entirely: it is a workspace of its
 * own and 200px of navigation would leave too little for the 2×2 grid. Large
 * screens keep it for every page, because there is room for the navigation and
 * the workspace side by side and hiding it would cost a click on every switch.
 *
 * Returned as Tailwind classes rather than a pixel number so the shell can pass
 * them through `cn` with the rest of the column's classes.
 *
 * Args:
 *   pathname: Current route
 *   isXL: Whether the window is at the large breakpoint
 *
 * Returns:
 *   str: `hidden` for a compact page on medium, otherwise the column width
 */
export function navColumnClass(pathname: string, isXL: boolean): string {
  if (isCompactPath(pathname) && !isXL) return "hidden";
  return isXL ? "w-60" : "w-50";
}

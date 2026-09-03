import { onMount } from "svelte";
import { goto } from "$app/navigation";
import { page } from "$app/state";

const ROUTE_TO_PATH: Record<string, string> = {
  setup: "/setup",
  loading: "/loading",
  app: "/app",
  error: "/error",
};

// Dev-only route preview: when the URL is on a startup route, the URL
// becomes the source of truth and shared-state navigation is suspended, so
// any launch page can be previewed without preparing its real conditions
// (broken config, missing python, failed backend...). The error payload can
// be injected through query params, e.g.
//   /error?errorKey=ConfigNotFound&errorPath=C%3A%5Cfoo
// The loading page is previewed through a backendSuccess flag:
//   /loading?backendSuccess=true  -> startup log, no failure hint
//   /loading?backendSuccess=false -> startup log + failure hint
// DevRouteSwitcher.svelte builds such URLs from its popover menu.
export function getDevOverride(): {
  route: string;
  errorKey?: string;
  errorPath?: string;
  backendSuccess?: boolean;
} | null {
  if (!import.meta.env.DEV) return null;
  const path = page.route.id;
  const route = Object.keys(ROUTE_TO_PATH).find((key) => ROUTE_TO_PATH[key] === path);
  if (!route) return null;
  const params = page.url.searchParams;
  const backendSuccess = params.get("backendSuccess");
  return {
    route,
    errorKey: params.get("errorKey") || undefined,
    errorPath: params.get("errorPath") || undefined,
    // Loading preview flag; ignored on other routes.
    backendSuccess:
      route === "loading" && (backendSuccess === "true" || backendSuccess === "false")
        ? backendSuccess === "true"
        : undefined,
  };
}

// Initial shared state, read synchronously once (module scope, via the
// preload sendSync bridge) so the very first paint already renders with
// the host's display theme instead of flashing the light fallback before
// the async getSharedState round trip resolves. Only a starting point:
// live updates come from onSharedStateUpdate.
let initialSharedState: any;

function getInitialSharedState(): any {
  if (initialSharedState === undefined) {
    try {
      initialSharedState = window.electronAPI.getSharedStateSync();
    } catch {
      initialSharedState = null;
    }
  }
  return initialSharedState;
}

function navigateTo(route: string) {
  // Dev preview: the URL already picks the page, never navigate away from it.
  if (getDevOverride()) return;
  const path = ROUTE_TO_PATH[route];
  if (path && page.route.id !== path) {
    goto(path);
  }
}

export function useSharedState() {
  let state = $state<any>(getInitialSharedState());

  onMount(() => {
    window.electronAPI.getSharedState().then((s: any) => {
      state = s;
      navigateTo(s.route);
    });

    const unsubscribe = window.electronAPI.onSharedStateUpdate((newState: any) => {
      state = newState;
      navigateTo(newState.route);
    });

    return unsubscribe;
  });

  return {
    get displayLang() {
      return state?.language || "en-US";
    },
    get displayTheme() {
      return state?.theme || "light";
    },
    get configLang() {
      return state?.configLang || "system";
    },
    get configTheme() {
      return state?.configTheme || "system";
    },
    get dpiScaling() {
      return state?.dpiScaling ?? true;
    },
    get backendPort() {
      return state?.backendPort || 22267;
    },
    get route() {
      return getDevOverride()?.route || state?.route || "loading";
    },
    get isFirstTimeSetup() {
      return state?.isFirstTimeSetup || false;
    },
    get backendStatus(): "starting" | "success" | "failed" {
      // Dev loading preview: the URL backendSuccess flag decides the value
      // (true -> "success", false -> "failed").
      const backendSuccess = getDevOverride()?.backendSuccess;
      if (backendSuccess !== undefined) return backendSuccess ? "success" : "failed";
      return state?.backendStatus ?? "starting";
    },
    get errorKey() {
      return getDevOverride()?.errorKey ?? state?.errorKey;
    },
    get errorPath() {
      return getDevOverride()?.errorPath ?? state?.errorPath;
    },
  };
}

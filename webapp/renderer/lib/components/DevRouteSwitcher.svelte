<script lang="ts">
  import FlaskConical from "@lucide/svelte/icons/flask-conical";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { buttonVariants } from "$lib/components/ui/button/button.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import { cn } from "$lib/utils";

  // Dev-only launcher: preview any startup route without preparing the real
  // launch conditions (broken config, missing python, failed backend...).
  // Error entries inject the payload through the URL, which useSharedState
  // reads in dev mode (see getDevOverride); the URL stays the source of
  // truth until it is navigated away. The module is only loaded in dev:
  // +layout.svelte imports it dynamically behind import.meta.env.DEV.
  // Labels are internal identifiers, so this dev tool deliberately does
  // not participate in i18n.
  const PAGES = [
    { label: "Setup", path: "/setup" },
    // Loading is previewed in two modes: backendSuccess=true (startup log
    // only) and backendSuccess=false (startup log + failure hint). The
    // flag goes into the URL and drives the preview through
    // getDevOverride; the loading page fills mock log lines for it.
    { label: "Loading (Success)", path: "/loading", backendSuccess: true },
    { label: "Loading (Failed)", path: "/loading", backendSuccess: false },
    { label: "App", path: "/app" },
  ];

  // Keys the error page resolves (mirrors the handled subset of
  // i18n/Error.json; backend startup failures are shown by the
  // loading/setup pages instead); "UnknownError" is the fallback when no
  // errorKey is present.
  const ERROR_KEYS = ["ConfigNotFound", "PythonNotConfigured", "PythonNotFound", "GuiPyNotFound"];

  // Sample path injected into the error URL so the error page's path
  // section has content to render in dev; a real path only exists when the
  // error actually happened.
  const SAMPLE_ERROR_PATH = "C:\\ProgramData\\Pycharm\\Alasio";

  const currentRoute = $derived(page.route.id);
  const currentErrorKey = $derived(page.url.searchParams.get("errorKey"));
  const currentBackendSuccess = $derived(page.url.searchParams.get("backendSuccess"));

  function navigate(path: string, query?: Record<string, string>) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value) params.set(key, value);
    }
    const qs = params.toString();
    goto(qs ? `${path}?${qs}` : path);
  }

  function isActive(path: string, backendSuccess?: boolean) {
    if (currentRoute !== path) return false;
    // Items with a backendSuccess flag only match when the URL carries the
    // same value.
    return backendSuccess === undefined ? true : currentBackendSuccess === String(backendSuccess);
  }
</script>

<div class="fixed right-4 bottom-4 z-50">
  <Popover.Root>
    <Popover.Trigger class={cn(buttonVariants({ variant: "secondary", size: "icon" }), "shadow-lg")}>
      <FlaskConical class="size-4" />
    </Popover.Trigger>
    <Popover.Content side="top" align="end" class="z-100 w-64 p-2">
      <div class="px-2 py-1.5 text-xs font-medium text-muted-foreground">Dev Routes</div>
      {#each PAGES as item}
        <Popover.Close
          class={cn(
            buttonVariants({ variant: "ghost", size: "sm" }),
            "w-full justify-start",
            isActive(item.path, item.backendSuccess) && "bg-accent text-accent-foreground",
          )}
          onclick={() =>
            navigate(
              item.path,
              item.backendSuccess !== undefined ? { backendSuccess: String(item.backendSuccess) } : undefined,
            )}
        >
          {item.label}
        </Popover.Close>
      {/each}

      <div class="mx-2 my-1 h-px bg-border"></div>

      <div class="px-2 py-1.5 text-xs font-medium text-muted-foreground">Error</div>
      {#each ERROR_KEYS as key}
        <Popover.Close
          class={cn(
            buttonVariants({ variant: "ghost", size: "sm" }),
            "w-full justify-start",
            currentRoute === "/error" && currentErrorKey === key && "bg-accent text-accent-foreground",
          )}
          onclick={() => navigate("/error", { errorKey: key, errorPath: SAMPLE_ERROR_PATH })}
        >
          {key}
        </Popover.Close>
      {/each}
      <Popover.Close
        class={cn(
          buttonVariants({ variant: "ghost", size: "sm" }),
          "w-full justify-start",
          currentRoute === "/error" && !currentErrorKey && "bg-accent text-accent-foreground",
        )}
        onclick={() => navigate("/error")}
      >
        UnknownError
      </Popover.Close>
    </Popover.Content>
  </Popover.Root>
</div>

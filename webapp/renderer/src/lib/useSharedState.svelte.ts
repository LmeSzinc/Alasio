import { onMount } from 'svelte';
import { goto } from '$app/navigation';
import { page } from '$app/state';

const ROUTE_TO_PATH: Record<string, string> = {
  setup: '/setup',
  loading: '/loading',
  app: '/app',
  error: '/error',
};

function navigateTo(route: string) {
  const path = ROUTE_TO_PATH[route];
  if (path && page.route.id !== path) {
    goto(path);
  }
}

export function useSharedState() {
  let state = $state<any>(null);

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
    get displayLang() { return state?.language || 'en-US'; },
    get displayTheme() { return state?.theme || 'light'; },
    get configLang() { return state?.configLang || 'system'; },
    get configTheme() { return state?.configTheme || 'system'; },
    get backendPort() { return state?.backendPort || 22267; },
    get route() { return state?.route || 'loading'; },
    get isFirstTimeSetup() { return state?.isFirstTimeSetup || false; },
    get errorMessage() { return state?.errorMessage; },
  };
}

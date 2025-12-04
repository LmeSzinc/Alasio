import { onMount } from 'svelte';

export function useSharedState() {
  let state = $state<any>(null);

  onMount(() => {
    window.electronAPI.getSharedState().then((s: any) => {
      state = s;
    });
    
    const unsubscribe = window.electronAPI.onSharedStateUpdate((newState: any) => {
      state = newState;
    });
    
    return unsubscribe;
  });

  return {
    get language() { return state?.language || 'en-US'; },
    get webuiPort() { return state?.webuiPort || 22267; },
    get route() { return state?.route || 'loading'; },
    get isFirstTimeSetup() { return state?.isFirstTimeSetup || false; },
    get errorMessage() { return state?.errorMessage; },
  };
}

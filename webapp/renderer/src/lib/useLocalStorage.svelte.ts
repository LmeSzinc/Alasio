export function useLocalStorage<T = string>(key: string, defaultValue: T) {
  function getStoredValue(): T {
    try {
      const item = localStorage.getItem(key);
      return item ? JSON.parse(item) : defaultValue;
    } catch {
      return defaultValue;
    }
  }

  let value = $state(getStoredValue());

  $effect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      console.error(`Error saving to localStorage[${key}]:`, error);
    }
  });

  $effect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === key && e.newValue !== null) {
        try {
          value = JSON.parse(e.newValue);
        } catch {
          value = defaultValue;
        }
      }
    };

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  });

  return {
    get value() { return value; },
    set value(v: T) { value = v; },
  };
}

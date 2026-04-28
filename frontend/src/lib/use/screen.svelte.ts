import { browser } from "$app/environment";

/**
 * Screen size utility for Svelte 5.
 * Tracks window.innerWidth and provides reactive derived properties.
 */
class Screen {
  width = $state(0);
  isHidden = $state(false);

  constructor() {
    if (browser) {
      this.width = window.innerWidth;
      this.isHidden = document.hidden;
      // Using a closure to keep 'this' context
      const onResize = () => {
        this.width = window.innerWidth;
      };
      const onVisibilityChange = () => {
        this.isHidden = document.hidden;
      };
      window.addEventListener("resize", onResize);
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
  }

  get isSM() {
    return this.width >= 640;
  }
  get isMD() {
    return this.width >= 768;
  }
  get isLG() {
    return this.width >= 1024;
  }
  get isXL() {
    return this.width >= 1280;
  }
  get isXXL() {
    return this.width >= 1536;
  }
  get isMDtoXL() {
    return this.width >= 768 && this.width < 1280;
  }
}

export const screen = new Screen();

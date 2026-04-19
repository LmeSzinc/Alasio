import { untrack } from "svelte";

// special value to avoid going to overview
const NAV_DEVICE = "__nav_device__";

class UIState {
  nav_name: string = $state("");
  card_name: string = $state("");
  card_scroll: string = $state("");
  card_indicate: string = $state("");
  opened_nav: string = $state("");
  flash_target: string = $state("");
  flash_trigger: number = $state(0);
  scroll_trigger: number = $state(0);

  get isOverview() {
    return !this.opened_nav;
  }

  get isDevice() {
    return this.opened_nav === NAV_DEVICE;
  }

  get isNav() {
    return this.opened_nav && !this.isDevice;
  }

  setNav(nav_name: string, card_name: string) {
    untrack(() => {
      this.nav_name = nav_name;
      this.card_name = card_name;
      this.card_scroll = card_name;
      this.card_indicate = card_name;
      this.opened_nav = nav_name;
      this.flash_target = "";
      this.flash_trigger = 0;
      this.scroll_trigger += 1;
    });
  }

  setDevice() {
    untrack(() => {
      this.nav_name = "";
      this.card_name = "";
      this.card_scroll = "";
      this.card_indicate = "";
      this.opened_nav = NAV_DEVICE;
      this.flash_target = "";
      this.flash_trigger = 0;
      this.scroll_trigger += 1;
    });
  }

  setOverview() {
    untrack(() => {
      this.nav_name = "";
      this.card_name = "";
      this.card_scroll = "";
      this.card_indicate = "";
      this.opened_nav = "";
      this.flash_target = "";
      this.flash_trigger = 0;
      this.scroll_trigger += 1;
    });
  }

  triggerFlash(target: string) {
    untrack(() => {
      this.flash_target = target;
      this.flash_trigger += 1;
    });
  }
}

export const uiState = new UIState();
export default UIState;

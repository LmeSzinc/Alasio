// special value to avoid going to overview
const NAV_DEVICE = "__nav_device__";

class UIState {
  nav_name: string;
  card_name: string;
  opened_nav: string;

  constructor() {
    this.nav_name = $state("");
    this.card_name = $state("");
    this.opened_nav = $state("");
  }

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
    this.nav_name = nav_name;
    this.card_name = card_name;
    this.opened_nav = nav_name;
  }

  setDevice() {
    this.nav_name = "";
    this.card_name = "";
    this.opened_nav = NAV_DEVICE;
  }

  setOverview() {
    this.nav_name = "";
    this.card_name = "";
    this.opened_nav = "";
  }
}

export const uiState = new UIState();
export default UIState;

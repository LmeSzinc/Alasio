// special value to avoid going to overview
const NAV_DEVICE = "__nav_device__";

class UIState {
  nav_name: string = $state("");
  card_name: string = $state("");
  card_scroll: string = $state("");
  card_indicate: string = $state("");
  opened_nav: string = $state("");

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
    this.card_indicate = card_name;
    this.opened_nav = nav_name;
  }

  setDevice() {
    this.nav_name = "";
    this.card_name = "";
    this.card_scroll = "";
    this.card_indicate = "";
    this.opened_nav = NAV_DEVICE;
  }

  setOverview() {
    this.nav_name = "";
    this.card_name = "";
    this.card_scroll = "";
    this.card_indicate = "";
    this.opened_nav = "";
  }
}

export const uiState = new UIState();
export default UIState;

class UIState {
  nav_name: string;
  card_name: string;
  opened_nav: string;

  constructor() {
    this.nav_name = $state("");
    this.card_name = $state("");
    this.opened_nav = $state("");
  }
}

export default UIState;

import { untrack } from "svelte";

// special value to avoid going to overview
const NAV_DEVICE = "__nav_device__";

// NAV 逻辑：
// nav 展开有多个 card title, card title 有主题色竖线的 indicator
// 1. 如果用户滚动内容列表，indicator 指示的 card title 跟随变化
// 2. 如果用户主动点击 card title，那么会滚动内容列表到对于的 card，indicator 指示的 card title 跟随变化
// 3. 如果用户双击 card title，那么会滚动内容列表，indicator跟随变化，内容列表对应的 card 会闪烁主题色边框
// 4. 由于内容列表 viewport 内可能有多个 card，indicator 只能跟随到 viewport 顶部的第一个 card，底部的 card 永远无法通过滚动内容列表实现 card title 指示
// 5. 如果用户主动点击底部的 card title，那么 indicator 会临时固定指示到用户点击的 card，尽管此时不满足第一点（indicator指示内容页的card）
//    这样更符合点击哪里指示哪里的直觉，并且避免了indicator闪烁的问题：
//        indicator指示到目标card title -> 列表滚动完成 -> 需要指示viewport顶部的card -> indicator快速转变为指示另一个card
// 6. 处于第五点描述的状态时（indicator临时固定指示到用户点击的 card），如果此时用户主动滚动了内容列表，那么改为遵循第一点（indicator指示内容页的card）
//    这样尊重了用户最后一次主动操作 而产生变化，更符合直觉
class UIState {
  nav_name: string = $state("");
  card_name: string = $state("");
  card_scroll: string = $state("");
  card_indicate: string = $state("");
  opened_nav: string = $state("");
  flash_target: string = $state("");
  flash_trigger: number = $state(0);
  // scroll_trigger value has meaning
  // <0: nav switch (scroll to top immediately, unique negative value each time)
  // >0: switching to another card within same nav (smooth scroll)
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
      if (this.nav_name === nav_name) {
        // Same-nav click: trigger must be positive for a smooth scroll with isScrolling guard.
        // A leftover negative value from the last nav switch would be treated as a nav-switch
        // (instant scroll, isScrolling never set) or collide with previous trigger values,
        // causing the scroll to be skipped and the indicator to flicker (point 5)
        if (this.scroll_trigger < 0) this.scroll_trigger = 0;
        this.scroll_trigger += 1;
      } else {
        // Use unique negative values so ArgCardList sees a new trigger every nav switch
        if (this.scroll_trigger > 0) this.scroll_trigger = 0;
        this.scroll_trigger -= 1;
      }
      this.nav_name = nav_name;
      this.card_name = card_name;
      this.card_scroll = card_name;
      this.card_indicate = card_name;
      this.opened_nav = nav_name;
      this.flash_target = "";
      this.flash_trigger = 0;
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
      this.scroll_trigger = 0;
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
      this.scroll_trigger = 0;
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

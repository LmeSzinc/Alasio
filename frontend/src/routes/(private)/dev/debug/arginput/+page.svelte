<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import ArgCardList from "$lib/components/arg/ArgCardList.svelte";
  import type { ArgData, CardData, InfoData } from "$lib/components/arg/utils.svelte";

  // --- Mock helpers ---

  /**
   * Build an arg of the mock config. Which arginput component renders an arg is
   * decided by `dt` (see $lib/components/arg/Arg.svelte), so every card below
   * groups the args handled by one component.
   */
  function arg(group: string, argName: string, dt: string, value: any, extra: Record<string, any> = {}): ArgData {
    return { task: "ArgInput", group, arg: argName, dt, value, ...extra };
  }

  function info(group: string, name: string, help?: string): InfoData {
    return { group, arg: "_info", card: `card-${group}`, name, help };
  }

  function makeCard(infoData: InfoData, groups: Record<string, Record<string, ArgData>> = {}): CardData {
    // Object spread of an index-signature type does not propagate the index
    // signature, so cast the literal to CardData (comparable in either direction).
    return { _info: infoData, ...groups } as CardData;
  }

  /**
   * Enable can't be displayed as a card body row: it is rendered as the card
   * badge with `NextRun` below it (see $lib/components/arg/CardEnable.svelte),
   * so a card needs a `Scheduler` group to show them.
   */
  function schedulerCard(card: string, name: string, help: string, enable: boolean): CardData {
    return makeCard(
      { group: "Scheduler", arg: "_info", card, name, help },
      {
        Scheduler: {
          Enable: arg("Scheduler", "Enable", "enable", enable, {
            // Same shape as a real config: dt=enable gets 'true' / 'false' options
            // and i18n labels injected by the backend
            option: ["true", "false"],
            option_i18n: { true: "ENABLED", false: "DISABLED" },
          }),
          NextRun: arg("Scheduler", "NextRun", "datetime", "2026-01-02T03:04:05Z"),
        },
      },
    );
  }

  // --- Mock data: one card per arginput component ---
  const mockData: Record<string, CardData> = {
    // dt=input / input-line / input-int / input-float / datetime all render as Input.svelte
    Input: makeCard(info("Input", "Input", "dt=input · input-line · input-int · input-float · datetime"), {
      Input: {
        Text: arg("Input", "Text", "input", "text value", { name: "input" }),
        // The box of dt=textarea on a single line: the value is edited by an
        // <input>, a newline can not be typed and never breaks the line
        Line: arg("Input", "Line", "input-line", "single line value", {
          name: "input-line",
          help: "Textarea-like box, one line: the backend gives it the vertical layout",
          layout: "vert",
        }),
        // range makes the validation error state reachable without editing data
        Integer: arg("Input", "Integer", "input-int", 128, { name: "input-int", ge: 0, le: 1000 }),
        Float: arg("Input", "Float", "input-float", 0.85, { name: "input-float" }),
        Datetime: arg("Input", "Datetime", "datetime", "2026-01-02T03:04:05Z", {
          name: "datetime",
          help: "Datetime is displayed in local time, the UTC offset shows on focus",
        }),
      },
    }),
    // dt=select shows option_i18n labels, options can be any literal type
    Select: makeCard(info("Select", "Select", "dt=select · dt=secondary-select · labels from option_i18n"), {
      Select: {
        Str: arg("Select", "Str", "select", "auto", {
          name: "select",
          option: ["auto", "manual", "disabled"],
          option_i18n: { auto: "Auto", manual: "Manual", disabled: "Disabled" },
        }),
        Int: arg("Select", "Int", "select", 1, { name: "select (int)", option: [1, 2, 3] }),
        // dt=secondary-select splits the options into groups: the left column
        // is a navigation aid (it is not stored), only an option commits
        Chapter: arg("Select", "Chapter", "secondary-select", "2-3", {
          name: "secondary-select",
          option_dict: {
            chapter1: ["1-1", "1-2", "1-3"],
            chapter2: ["2-1", "2-2", "2-3"],
            chapter3: ["3-1", "3-2", "3-3"],
            chapter4: ["4-1", "4-2", "4-3"],
            // A group name longer than the column, to check the truncation
            chapter_extra: ["5-1", "5-2", "5-3"],
          },
          // Group labels come from option_i18n as well, options fall back to
          // their own value when no translation is given
          option_i18n: {
            chapter1: "Chapter 1",
            chapter2: "Chapter 2",
            chapter3: "Chapter 3",
            chapter4: "Chapter 4",
            chapter_extra: "Chapter Extra Long Name",
          },
        }),
      },
    }),
    CheckboxStatic: makeCard(info("CheckboxStatic", "Checkbox & Static", "dt=checkbox on/off · dt=static read-only"), {
      CheckboxStatic: {
        On: arg("CheckboxStatic", "On", "checkbox", true, { name: "checkbox (on)" }),
        Off: arg("CheckboxStatic", "Off", "checkbox", false, { name: "checkbox (off)" }),
        Static: arg("CheckboxStatic", "Static", "static", "static value", {
          // dt=static gets its single allowed value as option from the backend
          option: ["static value"],
          name: "static",
        }),
      },
    }),
    // dt=textarea is displayed in vertical layout, as the backend sets `layout: vert`
    Textarea: makeCard(info("Textarea", "Textarea", "dt=textarea · vertical layout"), {
      Textarea: {
        Text: arg("Textarea", "Text", "textarea", "multi\nline\ntext", {
          name: "textarea",
          help: "Vertical layout: name, help, then input",
          layout: "vert",
        }),
      },
    }),
    // dt=filter-order shows the order as pills, the edit button of the title row
    // opens the transfer-style dialog (see doc/2026-10-03_filter-order.md)
    FilterOrder: makeCard(info("FilterOrder", "Filter Order", "dt=filter-order · pills + transfer-style editor"), {
      FilterOrder: {
        Order: arg("FilterOrder", "Order", "filter-order", ["Fleet-1", "Fleet-2", "Submarine"], {
          name: "filter-order",
          help: "Order from high to low priority, e.g. the sortie order of fleets",
          option: ["Fleet-1", "Fleet-2", "Fleet-3", "Fleet-4", "Submarine", "CallSubmarine"],
          // Demo of the option_i18n labels (real data is generated by the backend)
          option_i18n: { CallSubmarine: "Call Submarine" },
          // The backend sets `layout: vert` for filter-order
          layout: "vert",
        }),
        // dt=filter-order-item with secondary options: every primary item of
        // the value carries one of its own options as "item-option"
        Secondary: arg("FilterOrder", "Secondary", "filter-order-item", ["PlateT4-gun", "BookT3-red", "Chip"], {
          name: "filter-order (secondary)",
          help: "PlateT4 and BookT3 pick a type, Chip has none; the editor changes it in place",
          option: ["PlateT4", "BookT3", "Chip", "PR"],
          option_dict: {
            PlateT4: ["gun", "general", "torpedo", "antiair", "plane"],
            BookT3: ["red", "blue", "yellow"],
          },
          layout: "vert",
        }),
        // A long option list, with items that are not in `option` anymore (an
        // option was removed from the yaml after the value was set): the pills
        // render them and the editor keeps them in the selected column, where
        // they can be removed
        OrderLarge: arg(
          "FilterOrder",
          "OrderLarge",
          "filter-order",
          [
            "Fleet-2",
            "Unknown-1",
            "Fleet-6",
            "RemovedItem",
            "Fleet-1",
            "Fleet-4",
            "Fleet-5",
            "Submarine",
            "CallSubmarine",
            "ActionPoint",
            "PurpleCoins",
            "Oil",
            "Book",
            "Fragment",
            "GearDesignPlanT3",
            "GearDesignPlanT2",
            "GearDesignPlanT1",
            "OrdnanceTestingReportT3",
            "OrdnanceTestingReportT2",
            "OrdnanceTestingReportT1",
            "PlateRandomT4",
            "PlateRandomT3",
            "PlateRandomT2",
            "PlateRandomT1",
            "DevelopmentMaterialT3",
            "DevelopmentMaterialT2",
            "DevelopmentMaterialT1",
            "LoggerAbyssal",
            "LoggerAbyssalT5",
            "LoggerObscure",
          ],
          {
            name: "filter-order (long, unknown items)",
            help: "A long option list, and 30 of them are selected; Unknown-1 and RemovedItem are not in option",
            option: [
              "Fleet-1",
              "Fleet-2",
              "Fleet-3",
              "Fleet-4",
              "Fleet-5",
              "Fleet-6",
              "Submarine",
              "CallSubmarine",
              "ActionPoint",
              "PurpleCoins",
              "Oil",
              "Coin",
              "Book",
              "Fragment",
              "GearDesignPlanT3",
              "GearDesignPlanT2",
              "GearDesignPlanT1",
              "OrdnanceTestingReportT3",
              "OrdnanceTestingReportT2",
              "OrdnanceTestingReportT1",
              "PlateRandomT4",
              "PlateRandomT3",
              "PlateRandomT2",
              "PlateRandomT1",
              "DevelopmentMaterialT3",
              "DevelopmentMaterialT2",
              "DevelopmentMaterialT1",
              "LoggerAbyssal",
              "LoggerAbyssalT5",
              "LoggerObscure",
            ],
            layout: "vert",
          },
        ),
        // A long order with unknown items: a primary item that is not in
        // `option` anymore (Gone, RemovedItem), a secondary option that
        // was removed from `option_dict` (BookT2-gold), and plain items that
        // never had a secondary option (Chip, CatT3). The pills mark them, the
        // editor keeps their rows so they can be fixed or removed
        SecondaryLarge: arg(
          "FilterOrder",
          "SecondaryLarge",
          "filter-order-item",
          [
            "PlateT4-gun",
            "BookT3-red",
            "Gone-gun",
            "BookT2-gold",
            "PR-neptune",
            "Chip",
            "CatT3",
            "RemovedItem",
            "Retrofit-cl",
            "FoodT6",
            "FoodT5",
            "CatT2",
            "BoxT4",
            "Legacy-alpha",
            "Legacy-beta",
            "Retrofit-dd",
            "PR-monarch",
            "BookT3-blue",
            "PlateT4-antiair",
            "BookT2-red",
          ],
          {
            name: "filter-order (secondary, long, unknown items)",
            help: "Gone and RemovedItem are not in option, BookT2-gold is not declared, Chip has no secondary option",
            option: [
              "PlateT4",
              "BookT3",
              "PR",
              "CatT3",
              "Chip",
              "BookT2",
              "Retrofit",
              "FoodT6",
              "FoodT5",
              "CatT2",
              "BoxT4",
            ],
            option_dict: {
              PlateT4: ["gun", "general", "torpedo", "antiair", "plane"],
              BookT3: ["red", "blue", "yellow"],
              BookT2: ["red", "blue", "yellow"],
              PR: ["neptune", "monarch", "seattle", "georgia"],
              Retrofit: ["dd", "cl", "bb", "cv"],
            },
            layout: "vert",
          },
        ),
        // Long names on both levels: a primary item and a secondary option that
        // are wider than the pill and the dialog row, so the truncation of the
        // pill, the row label and the secondary select are all visible at once
        SecondaryLongNames: arg(
          "FilterOrder",
          "SecondaryLongNames",
          "filter-order-item",
          [
            "GearDesignPlanT3-OrdnanceTestingReportT3",
            "OrdnanceTestingReportT2-DevelopmentMaterialT2",
            "Short-cl",
            "PlateT4-gun",
          ],
          {
            name: "filter-order (secondary, long names)",
            help: "A long primary item and a long secondary option; Short-cl is the short case the selector keeps a min width for",
            option: [
              "GearDesignPlanT3",
              "OrdnanceTestingReportT2",
              "PlateT4",
              "Short",
              "Chip",
              "DevelopmentMaterialT1",
            ],
            option_dict: {
              GearDesignPlanT3: ["OrdnanceTestingReportT3", "DevelopmentMaterialT3", "cl"],
              OrdnanceTestingReportT2: ["DevelopmentMaterialT2", "OrdnanceTestingReportT1", "dd"],
              PlateT4: ["gun", "general", "torpedo"],
              Short: ["cl", "dd", "bb", "cv"],
              DevelopmentMaterialT1: ["GearDesignPlanT1", "GearDesignPlanT2", "PlateRandomT4"],
            },
            layout: "vert",
          },
        ),
        // dt=filter-order-pair: the whole "item-option" is the identity, so
        // several types of one item may be selected at once. The unused column
        // groups the unselected pairs under their item, the tokens are its last
        // group. ExtraDrill is fully selected, so its header stays at 3/3 with
        // no row under it
        Pair: arg(
          "FilterOrder",
          "Pair",
          "filter-order-pair",
          [
            "UrgentCube-1:30",
            "UrgentCube-3",
            "ExtraCube-0:30",
            "ExtraDrill-1",
            "ExtraDrill-2",
            "ExtraDrill-2:40",
            "BookT3-red",
            "Major",
            "shortest",
          ],
          {
            name: "filter-order (pair)",
            help: "Several types of one item coexist; ExtraDrill is fully selected and keeps its 3/3 header",
            option: ["UrgentCube", "ExtraCube", "ExtraDrill", "BookT3", "Major", "DailyChip"],
            option_dict: {
              UrgentCube: ["1:30", "1:45", "3", "6"],
              ExtraCube: ["0:30", "1:30", "3"],
              ExtraDrill: ["1", "2", "2:40"],
              BookT3: ["red", "yellow", "blue"],
            },
            tokens: ["shortest"],
            layout: "vert",
          },
        ),
        // dt=filter-order-pair with entries outside the definition: "shortest"
        // is not declared as a token here and Gone is not in option, so both the
        // pill line and the editor mark them
        PairUnknown: arg(
          "FilterOrder",
          "PairUnknown",
          "filter-order-pair",
          ["UrgentCube-1:30", "shortest", "Gone-1:30", "Major"],
          {
            name: "filter-order (pair, unknown items)",
            help: "shortest is not a declared token and Gone is not in option; both views mark them and saving is blocked",
            option: ["UrgentCube", "ExtraCube", "Major"],
            option_dict: {
              UrgentCube: ["1:30", "1:45", "3"],
              ExtraCube: ["0:30", "1:30"],
            },
            layout: "vert",
          },
        ),
      },
    }),
    // Enable is only displayed in the card badge, so both states are shown here
    EnableOn: schedulerCard("card-ArgInput-EnableOn", "Enable · On", "dt=enable · card badge, click to toggle", true),
    EnableOff: schedulerCard(
      "card-ArgInput-EnableOff",
      "Enable · Off",
      "dt=enable · card badge, click to toggle",
      false,
    ),
  };

  // --- Reset support ---

  /**
   * Card groups as [groupName, args] pairs, the `_info` pseudo group excluded.
   * Object spread of an index-signature type does not propagate the index
   * signature, so the cast is needed to iterate the groups of a CardData.
   */
  function groupEntries(card: CardData): [string, Record<string, ArgData>][] {
    return Object.entries(card).filter(([groupName]) => groupName !== "_info") as [string, Record<string, ArgData>][];
  }

  function pathKey(cardName: string, groupName: string, argName: string) {
    return `${cardName}/${groupName}/${argName}`;
  }

  // This page has no backend, so reset restores the mock values. They are
  // captured here, before $state() makes the mock data reactive.
  const DEFAULTS = new Map<string, any>();
  for (const [cardName, card] of Object.entries(mockData)) {
    for (const [groupName, groupData] of groupEntries(card)) {
      for (const [argName, argData] of Object.entries(groupData)) {
        DEFAULTS.set(pathKey(cardName, groupName, argName), argData.value);
      }
    }
  }

  let data = $state(mockData);

  /**
   * Path of an arg inside the mock data, matched by object identity, so that
   * reset reads the default value of the very arg that was reset.
   */
  function locate(argData: ArgData): string | undefined {
    for (const [cardName, card] of Object.entries(data)) {
      for (const [groupName, groupData] of groupEntries(card)) {
        for (const [argName, value] of Object.entries(groupData)) {
          if (value === argData) return pathKey(cardName, groupName, argName);
        }
      }
    }
    return undefined;
  }

  // --- Handlers ---

  // There is no RPC on this page: an edit only updates the mock data, the
  // payload that would be sent to the backend is logged instead.
  function handleEdit(argData: ArgData) {
    console.log("edit", argData.task, argData.group, argData.arg, argData.value);
  }

  function handleReset(argData: ArgData) {
    const key = locate(argData);
    if (key === undefined) return;
    argData.value = DEFAULTS.get(key);
  }

  function handleCardReset(cardInfo: InfoData) {
    const cardName = Object.entries(data).find(([, card]) => card._info.card === cardInfo.card)?.[0];
    if (cardName === undefined) return;
    for (const [groupName, groupData] of groupEntries(data[cardName])) {
      for (const [argName, argData] of Object.entries(groupData)) {
        const value = DEFAULTS.get(pathKey(cardName, groupName, argName));
        if (value !== undefined) argData.value = value;
      }
    }
  }
</script>

<div class="container mx-auto flex flex-col gap-6 overflow-auto p-6 pb-20">
  <div class="space-y-2">
    <h1 class="text-3xl font-bold">Arg Input</h1>
    <p class="text-sm text-muted-foreground">
      One card per arginput component, rendered by
      <code class="rounded bg-muted px-1">ArgCardList</code>. The values are mock data: an edit only updates the page
      state (the payload is logged to console) and the reset button restores the initial values, no backend request is
      made.
    </p>
  </div>

  <ArgCardList class="w-full" bind:data {handleEdit} {handleReset} {handleCardReset} />
</div>

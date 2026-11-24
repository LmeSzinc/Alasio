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
    // Three ordered filter datatypes, one group each: a normal value and an
    // edge case. The edge case always carries the three things the views have
    // to survive: an entry outside the definition (red triangle), a repeated
    // entry (amber copy mark, saving blocked), and a name long enough to
    // truncate.
    FilterOrder: makeCard(
      info("FilterOrder", "Filter Order", "dt=filter-order / -item / -pair · pills + transfer-style editor"),
      {
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
          // A long value with a repeated entry (Fleet-1) and two entries that are
          // not in `option` anymore: the pills truncate at five lines and mark
          // both kinds of problem, the editor keeps every row so it can be fixed
          OrderEdge: arg(
            "FilterOrder",
            "OrderEdge",
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
              "Fleet-1",
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
              name: "filter-order (edge)",
              help: "31 entries: Fleet-1 repeats, Unknown-1 and RemovedItem are not in option, the last names are long",
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
        },
        FilterOrderItem: {
          // dt=filter-order-item: every primary item of the value carries one of
          // its own options as "item-option", the primary item is used once
          Item: arg("FilterOrder", "Item", "filter-order-item", ["PlateT4-gun", "BookT3-red", "Chip"], {
            name: "filter-order-item",
            help: "PlateT4 and BookT3 pick a type, Chip has none; the editor changes it in place",
            option: ["PlateT4", "BookT3", "Chip", "PR"],
            option_dict: {
              PlateT4: ["gun", "general", "torpedo", "antiair", "plane"],
              BookT3: ["red", "blue", "yellow"],
            },
            layout: "vert",
          }),
          // A primary item that is not in `option` anymore (Gone, RemovedItem), a
          // secondary option that was removed (BookT2-gold), plain items that
          // never had one (Chip, CatT3), the pair PlateT4-gun repeated, and long
          // names on both levels
          ItemEdge: arg(
            "FilterOrder",
            "ItemEdge",
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
              "PlateT4-gun",
              "GearDesignPlanT3-OrdnanceTestingReportT3",
              "GearDesignPlanT3-DevelopmentMaterialT3",
              "OrdnanceTestingReportT2-OrdnanceTestingReportT1",
              "Short-cl",
              "BookT3-blue",
              "BookT2-red",
            ],
            {
              name: "filter-order-item (edge)",
              help: "PlateT4-gun repeats, Gone and RemovedItem are not in option, BookT2-gold is not declared, the last names are long",
              option: [
                "PlateT4",
                "BookT3",
                "BookT2",
                "PR",
                "CatT3",
                "Chip",
                "Retrofit",
                "FoodT6",
                "FoodT5",
                "CatT2",
                "BoxT4",
                "GearDesignPlanT3",
                "OrdnanceTestingReportT2",
                "Short",
              ],
              option_dict: {
                PlateT4: ["gun", "general", "torpedo", "antiair", "plane"],
                BookT3: ["red", "blue", "yellow"],
                BookT2: ["red", "blue", "yellow"],
                PR: ["neptune", "monarch", "seattle", "georgia"],
                Retrofit: ["dd", "cl", "bb", "cv"],
                GearDesignPlanT3: ["OrdnanceTestingReportT3", "DevelopmentMaterialT3", "cl"],
                OrdnanceTestingReportT2: ["OrdnanceTestingReportT1", "DevelopmentMaterialT2", "dd"],
                Short: ["cl", "dd", "bb", "cv"],
              },
              layout: "vert",
            },
          ),
        },
        FilterOrderPair: {
          // dt=filter-order-pair: the whole "item-option" is the identity, so
          // several types of one item may be selected at once. The unused column
          // groups the unselected pairs under their item, the optionCommand are its last
          // group. ExtraDrill is fully selected, so its header stays at 0/3 with
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
              name: "filter-order-pair",
              help: "Several types of one item coexist; ExtraDrill is fully selected and keeps its 0/3 header",
              option: ["UrgentCube", "ExtraCube", "ExtraDrill", "BookT3", "Major", "DailyChip"],
              option_dict: {
                UrgentCube: ["1:30", "1:45", "3", "6"],
                ExtraCube: ["0:30", "1:30", "3"],
                ExtraDrill: ["1", "2", "2:40"],
                BookT3: ["red", "yellow", "blue"],
              },
              option_command: ["shortest"],
              layout: "vert",
            },
          ),
          // The pair UrgentCube-1:30 repeated, Gone-1:30 outside the definition,
          // an undeclared command (reset) and long names on both levels; shortest
          // is declared, so it stays a legal entry next to them
          PairEdge: arg(
            "FilterOrder",
            "PairEdge",
            "filter-order-pair",
            [
              "UrgentCube-1:30",
              "UrgentCube-1:30",
              "Gone-1:30",
              "Major",
              "shortest",
              "reset",
              "BookT3-yellow",
              "GearDesignPlanT3-OrdnanceTestingReportT3",
              "ExtraCube-1:30",
              "ExtraCube-1:30",
              "GearDesignPlanT3-DevelopmentMaterialT3",
            ],
            {
              name: "filter-order-pair (edge)",
              help: "UrgentCube-1:30 and ExtraCube-1:30 repeat, Gone-1:30 is not in option, reset is not a declared command, the last names are long",
              option: ["UrgentCube", "ExtraCube", "BookT3", "Major", "GearDesignPlanT3", "DailyChip"],
              option_dict: {
                UrgentCube: ["1:30", "1:45", "3"],
                ExtraCube: ["0:30", "1:30"],
                BookT3: ["red", "yellow", "blue"],
                GearDesignPlanT3: ["OrdnanceTestingReportT3", "DevelopmentMaterialT3", "cl"],
              },
              option_command: ["shortest"],
              layout: "vert",
            },
          ),
        },
      },
    ),
    // dt=filter-regex shows the pills of the decoded items and opens the single
    // column editor: every row is an inline level selector, the last row is the
    // always visible constructor (see FilterRegexDialog.svelte)
    FilterRegex: makeCard(
      info("FilterRegex", "Filter Regex", "dt=filter-regex · single column, inline level selects"),
      {
        FilterRegex: {
          // The levels of a research filter: the series, the family (a genre, a
          // rarity or a ship) and the detail (a duration or a project number).
          // The keys of a level table are decoded by the backend, the frontend
          // looks them up by level values
          Filter: arg("FilterRegex", "Filter", "filter-regex", ["S9-DR-0.5", "S9-E-315", "Q-1", "0.5", "reset"], {
            name: "filter-regex",
            help: "Research priority, three levels decoded by pattern; reset is a command",
            levels: [
              { name: "series", option: [{ key: {}, option: ["S8", "S9"] }] },
              {
                name: "family",
                option: [
                  { key: {}, option: ["B", "C", "D", "Q"] },
                  { key: { series: "S9" }, option: ["B", "C", "D", "Q", "agir"] },
                ],
              },
              {
                name: "detail",
                option: [
                  { key: {}, option: ["0.5", "1", "2"] },
                  { key: { family: "B" }, option: ["4"] },
                  { key: { family: "Q" }, option: ["0.5", "1", "2", "4", "002", "010"] },
                  { key: { series: "S9", family: "E" }, option: ["2", "031", "315"] },
                ],
              },
            ],
            pattern: "(?<series>S[1-9])?-?(?<family>DR|PRY|agir|[BCDEGHQT])?-?(?<detail>\\d{3}|\\d\\.\\d|\\d\\d?)?",
            option_command: ["reset"],
            option_i18n: { S9: "Series 9", DR: "DR ship", "0.5": "0.5 h", agir: "Agir", reset: "reset" },
            layout: "vert",
          }),
          // A repeated item, an item the pattern can not read (S9-X-1), an
          // undeclared command (shortest) and names long enough to truncate
          FilterEdge: arg(
            "FilterRegex",
            "FilterEdge",
            "filter-regex",
            [
              "S9-DR-0.5",
              "S9-DR-0.5",
              "S9-X-1",
              "S9-E-315",
              "reset",
              "shortest",
              "GearDesignPlanT3-OrdnanceTestingReportT3",
            ],
            {
              name: "filter-regex (edge)",
              help: "S9-DR-0.5 repeats, S9-X-1 is not decoded by pattern, shortest is not a declared command",
              levels: [
                { name: "series", option: [{ key: {}, option: ["S8", "S9"] }] },
                {
                  name: "family",
                  option: [
                    { key: {}, option: ["B", "C", "D", "Q"] },
                    { key: { series: "S9" }, option: ["B", "C", "D", "Q", "agir"] },
                  ],
                },
                {
                  name: "detail",
                  option: [
                    { key: {}, option: ["0.5", "1", "2"] },
                    { key: { family: "B" }, option: ["4"] },
                    { key: { series: "S9", family: "E" }, option: ["2", "031", "315"] },
                  ],
                },
              ],
              pattern: "(?<series>S[1-9])?-?(?<family>DR|PRY|agir|[BCDEGHQT])?-?(?<detail>\\d{3}|\\d\\.\\d|\\d\\d?)?",
              option_command: ["reset"],
              layout: "vert",
            },
          ),
        },
      },
    ),
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

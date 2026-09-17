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
    // dt=input / input-int / input-float / datetime all render as Input.svelte
    Input: makeCard(info("Input", "Input", "dt=input · input-int · input-float · datetime"), {
      Input: {
        Text: arg("Input", "Text", "input", "text value", { name: "input" }),
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
    Select: makeCard(info("Select", "Select", "dt=select · labels from option_i18n"), {
      Select: {
        Str: arg("Select", "Str", "select", "auto", {
          name: "select",
          option: ["auto", "manual", "disabled"],
          option_i18n: { auto: "Auto", manual: "Manual", disabled: "Disabled" },
        }),
        Int: arg("Select", "Int", "select", 1, { name: "select (int)", option: [1, 2, 3] }),
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

  function handleGroupReset(cardInfo: InfoData) {
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
    <p class="text-muted-foreground text-sm">
      One card per arginput component, rendered by
      <code class="bg-muted rounded px-1">ArgCardList</code>. The values are mock data: an edit only updates the page
      state (the payload is logged to console) and the reset button restores the initial values, no backend request is
      made.
    </p>
  </div>

  <ArgCardList class="w-full" bind:data {handleEdit} {handleReset} {handleGroupReset} />
</div>

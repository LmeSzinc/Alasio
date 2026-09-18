<script lang="ts">
  import { tick } from "svelte";
  import Check from "@lucide/svelte/icons/check";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { type InputProps, getArgName, useArgValue } from "$lib/components/arg/utils.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import { cn } from "$lib/utils";

  let { data = $bindable(), class: className, handleEdit, isDesc = false }: InputProps = $props();

  const arg = $derived(useArgValue<string>(data));

  // Group names are only a navigation aid for long option lists, they carry no
  // meaning in the stored value: only clicking an option commits.
  const optionDict = $derived<Record<string, any[]>>(data.option_dict ?? {});
  // Keep the order the backend generated (chapter1..chapter15): sorting the
  // keys would order "chapter10" before "chapter2".
  const groups = $derived(Object.keys(optionDict));

  const uid = $props.id();
  const listboxId = `${uid}-listbox`;
  const panelId = `${uid}-panel`;
  const tabId = (index: number) => `${uid}-tab-${index}`;
  const optionId = (index: number) => `${uid}-option-${index}`;

  let open = $state(false);
  let triggerEl = $state<HTMLElement | null>(null);
  let tablistEl = $state<HTMLElement | null>(null);
  let listboxEl = $state<HTMLElement | null>(null);

  // The options column shows `activeGroup` (the preview) while the selected
  // value stays untouched until an option is clicked.
  let activeGroup = $state<string | null>(null);
  let highlighted = $state(0);
  let focusColumn = $state<"group" | "option">("option");

  const options = $derived(activeGroup ? (optionDict[activeGroup] ?? []) : []);
  const activeTabIndex = $derived(Math.max(groups.indexOf(activeGroup ?? ""), 0));
  const triggerContent = $derived(arg.getLabel(arg.value));

  function groupOfValue(value: any): string | null {
    return groups.find((group) => optionDict[group]?.includes(value)) ?? groups[0] ?? null;
  }

  function clamp(index: number, max: number) {
    return Math.min(Math.max(index, 0), max);
  }

  function elementAt(parent: HTMLElement | null, index: number) {
    return parent?.querySelector<HTMLElement>(`[data-index="${index}"]`) ?? null;
  }

  /** Show `group` in the options column, without committing anything. */
  function previewGroup(group: string) {
    activeGroup = group;
    // Keep the selection in sight when the group holds it, else start at the top
    const index = (optionDict[group] ?? []).findIndex((option) => option === arg.value);
    highlighted = Math.max(index, 0);
  }

  function focusActiveTab() {
    elementAt(tablistEl, activeTabIndex)?.focus?.();
  }

  function focusOptionColumn() {
    focusColumn = "option";
    listboxEl?.focus?.();
  }

  function moveVertical(delta: number) {
    if (focusColumn === "group") {
      if (!groups.length) return;
      const next = clamp(activeTabIndex + delta, groups.length - 1);
      const group = groups[next];
      if (group === undefined || group === activeGroup) return;
      previewGroup(group);
      focusActiveTab();
      return;
    }
    if (!options.length) return;
    highlighted = clamp(highlighted + delta, options.length - 1);
  }

  function moveToEdge(edge: "first" | "last") {
    if (focusColumn === "group") {
      const group = edge === "first" ? groups[0] : groups[groups.length - 1];
      if (group === undefined) return;
      previewGroup(group);
      focusActiveTab();
      return;
    }
    if (!options.length) return;
    highlighted = edge === "first" ? 0 : options.length - 1;
  }

  function onContentKeydown(event: KeyboardEvent) {
    if (event.defaultPrevented) return;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp":
        event.preventDefault();
        moveVertical(event.key === "ArrowDown" ? 1 : -1);
        return;
      case "ArrowLeft":
        if (focusColumn === "option") {
          event.preventDefault();
          focusColumn = "group";
          focusActiveTab();
        }
        return;
      case "ArrowRight":
        if (focusColumn === "group") {
          event.preventDefault();
          focusOptionColumn();
        }
        return;
      case "Home":
      case "End":
        event.preventDefault();
        moveToEdge(event.key === "Home" ? "first" : "last");
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        if (focusColumn === "group") {
          focusOptionColumn();
        } else {
          const option = options[highlighted];
          if (option !== undefined) commit(option);
        }
        return;
    }
  }

  function commit(option: any) {
    arg.value = option;
    arg.submit(handleEdit);
    open = false;
  }

  // Same close behavior as dt=select: the trigger is blurred once the popup is
  // gone, so the focus ring does not stay behind after a selection.
  function onOpenChangeComplete(isOpen: boolean) {
    if (isOpen) return;
    setTimeout(() => triggerEl?.blur?.(), 0);
  }

  // The popup opens on the group of the current value. This runs before the
  // content is painted, so the first frame already shows the right group.
  $effect.pre(() => {
    if (!open) return;
    const group = groupOfValue(arg.value);
    if (group !== null) previewGroup(group);
    focusColumn = "option";
  });

  // Keep the highlighted option visible while changing group or moving with
  // the keyboard. `block: "nearest"` only scrolls the options column, never
  // the page behind the popup.
  $effect(() => {
    if (!open) return;
    // Read the group as well: switching group must scroll even when the
    // highlighted index happens to be the same
    if (!activeGroup) return;
    const index = highlighted;
    tick().then(() => {
      if (!open) return;
      elementAt(listboxEl, index)?.scrollIntoView?.({ block: "nearest" });
    });
  });
</script>

<div class={cn("w-full", className)}>
  <Popover.Root bind:open {onOpenChangeComplete}>
    <Popover.Trigger
      class={cn(
        "group bg-card relative flex h-7! w-full items-center gap-1 rounded-md border-0 p-1 pl-2 shadow-none",
        "focus:shadow-none",
        "focus:ring-ring focus:ring-offset-background focus:z-10 focus:ring-2 focus:ring-offset-5",
        // Focus moves into the popup, so the trigger cannot show a real :focus
        // ring; the open state draws the same ring a focused trigger would.
        "data-[state=open]:ring-offset-background data-[state=open]:z-10 data-[state=open]:shadow-none data-[state=open]:ring-offset-5",
        "data-[state=open]:ring-ring data-[state=open]:ring-2",
        "transition-shadow duration-200",
      )}
      bind:ref={triggerEl}
    >
      <span class="flex-1 truncate text-left">
        {triggerContent}
      </span>
      <ChevronDown class="text-muted-foreground pointer-events-none size-4 shrink-0" />
      <!-- Draw bottom border with peer -->
      <div
        class={cn(
          "group-focus:border-foreground/35 absolute right-0 bottom-0 left-0 border-b-2 transition-colors duration-200",
          isDesc ? "group-hover:border-primary border-transparent" : "border-primary",
        )}
      ></div>
    </Popover.Trigger>

    <Popover.Content
      class={cn(
        // The options column is as wide as the trigger, the groups column is
        // half of it. Height is not capped by a fixed value: the columns are
        // capped to the space the floating layer found inside the viewport.
        "w-[calc(var(--bits-floating-anchor-width)*1.5)] overflow-hidden p-0",
      )}
      sideOffset={4}
      // Right edge aligned with the trigger, like the dt=select popup
      align="end"
      // Reserve the app header (48px, AppHeader h-12) on top of the collision
      // boundary, the same way dt=select content does.
      collisionPadding={{ top: 56, right: 8, bottom: 8, left: 8 }}
      onkeydown={onContentKeydown}
    >
      {#if groups.length > 0}
        <div class="grid grid-cols-[0.5fr_1fr] text-sm">
          <div
            role="tablist"
            aria-orientation="vertical"
            tabindex={-1}
            bind:this={tablistEl}
            onpointerenter={() => (focusColumn = "group")}
            class="border-border max-h-(--bits-floating-available-height) overflow-y-auto overscroll-contain border-r py-1"
          >
            {#each groups as group, index (group)}
              <button
                type="button"
                role="tab"
                id={tabId(index)}
                aria-selected={group === activeGroup}
                aria-controls={panelId}
                tabindex={-1}
                data-index={index}
                class={cn(
                  "flex w-full cursor-default items-center gap-1 px-2 py-1 text-left outline-hidden",
                  "hover:bg-accent hover:text-accent-foreground",
                  group === activeGroup ? "bg-accent text-accent-foreground" : "text-muted-foreground",
                )}
                onclick={() => {
                  // Safari does not focus a button on click, so the column is
                  // tracked here instead of relying on the focus event.
                  focusColumn = "group";
                  previewGroup(group);
                }}
              >
                <span class="flex-1 truncate">{arg.getLabel(group)}</span>
                {#if focusColumn === "group" && index === activeTabIndex}
                  <ChevronRight class="size-3.5 shrink-0 opacity-70" />
                {/if}
              </button>
            {/each}
          </div>

          <div role="tabpanel" id={panelId} aria-labelledby={tabId(activeTabIndex)} class="min-w-0">
            <div
              role="listbox"
              id={listboxId}
              aria-label={getArgName(data)}
              aria-activedescendant={options.length ? optionId(highlighted) : undefined}
              tabindex={0}
              bind:this={listboxEl}
              onfocus={() => (focusColumn = "option")}
              onpointerenter={() => (focusColumn = "option")}
              class="max-h-(--bits-floating-available-height) overflow-y-auto overscroll-contain py-1 outline-hidden"
            >
              {#each options as option, index (option)}
                <!-- Selection by keyboard is handled by the listbox container
                     (aria-activedescendant pattern), so an option needs no key
                     handler of its own and must stay out of the tab order -->
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <div
                  role="option"
                  id={optionId(index)}
                  aria-selected={option === arg.value}
                  tabindex={-1}
                  data-index={index}
                  class={cn(
                    "flex cursor-default items-center gap-2 px-2 py-1",
                    index === highlighted && "bg-accent text-accent-foreground",
                  )}
                  onclick={() => commit(option)}
                  onpointermove={() => (highlighted = index)}
                >
                  <span class="flex-1 truncate">{arg.getLabel(option)}</span>
                  <!-- Same column indicator as the groups column, so it is
                       visible which column the arrow keys act on -->
                  {#if focusColumn === "option" && index === highlighted}
                    <ChevronRight class="size-3.5 shrink-0 opacity-70" />
                  {/if}
                  {#if option === arg.value}
                    <Check class="size-3.5 shrink-0" />
                  {/if}
                </div>
              {/each}
            </div>
          </div>
        </div>
      {:else}
        <div class="text-muted-foreground p-2 text-sm">No options available</div>
      {/if}
    </Popover.Content>
  </Popover.Root>
</div>

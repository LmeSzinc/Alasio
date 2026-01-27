<script lang="ts">
  import type { Component } from "svelte";
  import Checkbox from "../arginput/Checkbox.svelte";
  import Input from "../arginput/Input.svelte";
  import Select from "../arginput/Select.svelte";
  import Textarea from "../arginput/Textarea.svelte";
  import HorizontalLayout from "./HorizontalLayout.svelte";
  import type { ArgProps, InputProps, LayoutProps } from "./utils.svelte";
  import VerticalLayout from "./VerticalLayout.svelte";
  import VerticalReverseLayout from "./VerticalReverseLayout.svelte";

  let { data = $bindable(), ...restProps }: ArgProps = $props();

  // --- MAPPING LOGIC ---
  const componentMap: Record<string, Component<InputProps>> = {
    input: Input,
    checkbox: Checkbox,
    select: Select,
    textarea: Textarea,
    filter: Textarea,
  };
  const layoutMap: Record<string, Component<LayoutProps>> = {
    filter: VerticalLayout,
  };
  const layoutAliasMap: Record<string, Component<LayoutProps>> = {
    hori: HorizontalLayout,
    vert: VerticalLayout,
    "vert-rev": VerticalReverseLayout,
  };

  // --- COMPONENT RESOLUTION ---
  const InputComponent = $derived(componentMap[data.dt] || Input);
  const LayoutComponent = $derived.by(() => {
    // Priority 1: Use `data.layout` if it's provided AND maps to a known layout component.
    if (data.layout && layoutAliasMap[data.layout]) {
      return layoutAliasMap[data.layout];
    }
    // Priority 2: If the above fails, try to find a default layout based on `data.dt`.
    if (data.dt && layoutMap[data.dt]) {
      return layoutMap[data.dt];
    }
    // Priority 3: As a final fallback, use the system's hardcoded default layout.
    return HorizontalLayout;
  });
</script>

<!-- Pass all props, including parentWidth, down to the chosen layout -->
<LayoutComponent bind:data {InputComponent} {...restProps} />

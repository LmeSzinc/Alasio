import type { Component } from "svelte";

export type ArgData = {
  dt: string;
  value: any;
  name?: string;
  help?: string;
  layout?: string;
  [key: string]: any;
};

export type InputProps = {
  data: ArgData;
  class?: string;
  handleEdit?: (data: ArgData) => void;
  handleReset?: (data: ArgData) => void;
};

export type LayoutProps = InputProps & {
  parentWidth?: number;
  InputComponent: Component<InputProps>;
};

export type ArgProps = InputProps & {
  parentWidth?: number;
};

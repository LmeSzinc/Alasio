import { type ArgData, validateByConstraints, validateByDataType } from "$lib/components/arg/utils.svelte";

/**
 * One arg edit of the ConfigArg.batch_set rpc: the shape the backend accepts
 * for a batch of settings that must be saved together or not at all.
 */
export type DashboardEditEvent = {
  task: string;
  group: string;
  arg: string;
  value: any;
};

/**
 * One field of a dashboard group as the edit dialog commits it: the arg of the
 * page (its metadata, not its value) and the validated value to save.
 */
export type DashboardChange = {
  data: ArgData;
  value: any;
};

/**
 * The datatypes edited as text: their input holds a string while typing, so the
 * value has to be parsed and checked before it is saved.
 *
 * Every other datatype (select, checkbox, ...) is committed by its own
 * component as the value it is, and never passes a text.
 */
const TEXT_TYPES = new Set(["input-int", "input-float", "datetime"]);

/**
 * The arg a dashboard group counts: what its record holds, and what the time of
 * the group timestamps.
 *
 * A record group counts `Value`; a planner has no single amount, its progress
 * is the number the record is about.
 *
 * Args:
 *   groupData (Record<string, ArgData>): Args of one dashboard group
 *
 * Returns:
 *   (str | undefined): Name of the record arg, undefined for a group without one
 */
export function dashboardRecordArg(groupData: Record<string, ArgData>): string | undefined {
  if (groupData.Value) return "Value";
  if (groupData.Progress) return "Progress";
  return undefined;
}

/**
 * The editable args of one dashboard group, in display order.
 *
 * A dashboard group carries the numbers it shows (the amount of a record, the
 * Progress and Eta of a planner, the totals that go with them) and the settings
 * of the group itself (e.g. the Control, Recover and Oath of an Emotion), and
 * the record time. All of them are editable, so they are exactly the args
 * returned here.
 *
 * Left out:
 * - `_info`, the metadata row of the group;
 * - an arg of `dt: static`, a read-only row;
 * - the Time of a group without a record arg, which is only the publication time
 *   of the row.
 *
 * Args:
 *   groupData (Record<string, ArgData>): Args of one dashboard group
 *
 * Returns:
 *   (ArgData[]): The editable args, the record timestamp last
 */
export function dashboardFields(groupData: Record<string, ArgData>): ArgData[] {
  const hasRecord = dashboardRecordArg(groupData) !== undefined;
  const fields: ArgData[] = [];
  let time: ArgData | undefined;
  for (const [argKey, data] of Object.entries(groupData)) {
    if (argKey === "_info") continue;
    // A static row shows a value nobody can edit
    if (data.dt === "static") continue;
    if (argKey === "Time") {
      if (hasRecord) time = data;
      continue;
    }
    fields.push(data);
  }
  // The timestamp is the tail of the record, the values it timestamps come first
  if (time) fields.push(time);
  return fields;
}

/**
 * Parse a field of the edit dialog, in the datatype and constraints the backend
 * sends for that arg (input-int / input-float with ge / le / ..., datetime).
 *
 * A non-text field is returned as it is: its own input only ever commits a value
 * of the declared type.
 *
 * Args:
 *   value (any): Value the draft holds (a number once the input committed it,
 *     a string while it is being typed)
 *   data (ArgData): Arg metadata, its dt and constraints
 *
 * Returns:
 *   ({ value?: any; error: string | null }): The parsed value, or the error
 *   message of the first failure
 */
export function parseDashboardField(value: any, data: ArgData): { value?: any; error: string | null } {
  if (!TEXT_TYPES.has(data.dt)) return { value, error: null };

  const typed = validateByDataType(value, data.dt);
  if (typed.error) return { error: typed.error };

  const constraint = validateByConstraints(typed.value, data);
  if (constraint) return { error: constraint };

  return { value: typed.value, error: null };
}

/**
 * Compare two values of a field, tolerating the string form a text input holds
 * while it is being typed ("119" is the stored 119).
 *
 * Args:
 *   a (any): First value
 *   b (any): Second value
 *
 * Returns:
 *   (bool): True when the two name the same value
 */
export function sameValue(a: any, b: any): boolean {
  if (a === b) return true;
  // null / undefined never equal a number, they are a missing value
  if (a === null || a === undefined || b === null || b === undefined) return false;
  if (typeof a === "number" || typeof b === "number") {
    const left = Number(a);
    const right = Number(b);
    return !isNaN(left) && !isNaN(right) && left === right;
  }
  return false;
}

/**
 * Compare two datetime values by instant, not by string: the browser parses the
 * stored value and the input value differently ("...Z" and "...000Z"), so a
 * plain string comparison would call an untouched time dirty.
 *
 * Args:
 *   a (any): First datetime, any value the backend may send
 *   b (any): Second datetime
 *
 * Returns:
 *   (bool): True when both name the same instant
 */
export function sameInstant(a: any, b: any): boolean {
  const left = new Date(a).getTime();
  const right = new Date(b).getTime();
  if (isNaN(left) || isNaN(right)) return a === b;
  return left === right;
}

/**
 * Compare a draft field with the value the dialog opened with, using the
 * comparison its datatype needs.
 *
 * Args:
 *   draft (ArgData): Field of the draft
 *   original (ArgData): Same field as the dialog opened
 *
 * Returns:
 *   (bool): True when the field is unchanged
 */
export function sameField(draft: ArgData, original: ArgData): boolean {
  if (draft.dt === "datetime") return sameInstant(draft.value, original.value);
  return sameValue(draft.value, original.value);
}

/**
 * The batch that saves a record: every changed field is one edit of the same
 * call, so a value and its time are never written apart. Sent alone, post_edit()
 * of the model would recover the value from the stored time (or the time from
 * the new value), which is not what the user typed.
 *
 * Args:
 *   changes (DashboardChange[]): Changed fields of the dialog, with the values
 *     to save
 *
 * Returns:
 *   (DashboardEditEvent[]): The events of the batch
 */
export function dashboardEditEvents(changes: DashboardChange[]): DashboardEditEvent[] {
  return changes.map(({ data, value }) => ({
    task: data.task,
    group: data.group,
    arg: data.arg,
    value,
  }));
}

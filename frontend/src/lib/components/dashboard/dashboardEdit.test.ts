/**
 * Tests for the pure helpers of the dashboard group editor (dashboardEdit.ts).
 *
 * The editor itself (DashboardEdit.svelte / DashboardEditDialog.svelte) is
 * driven in DashboardEdit.svelte.test.ts; here the field selection, the parsing,
 * the dirty comparison and the batch shape are pinned without a DOM.
 */
import { describe, expect, it } from "vitest";
import type { ArgData } from "$lib/components/arg/utils.svelte";
import { t } from "$lib/i18n";
import {
  dashboardEditEvents,
  dashboardFields,
  dashboardRecordArg,
  parseDashboardField,
  sameField,
  sameInstant,
  sameValue,
} from "./dashboardEdit";

function arg(overrides: Partial<ArgData> = {}): ArgData {
  return {
    task: "Dashboard",
    group: "Gems",
    arg: "Value",
    dt: "input-int",
    value: 0,
    ge: 0,
    ...overrides,
  };
}

function info(dashboard: string): ArgData {
  return { task: "Dashboard", group: dashboard, arg: "_info", dt: "", value: "", dashboard };
}

function time(value = "2026-11-26T08:00:00Z"): ArgData {
  return arg({ arg: "Time", dt: "datetime", value, ge: undefined, le: undefined });
}

/** The arg names of a field list, in order */
function names(fields: ArgData[]): string[] {
  return fields.map((field) => field.arg);
}

describe("TestDashboardFields", () => {
  it("lists the Value and the Time of an Amount, the timestamp last", () => {
    const group = {
      _info: info("Amount"),
      Time: time(),
      Value: arg({ value: 119 }),
    };

    expect(names(dashboardFields(group))).toEqual(["Value", "Time"]);
  });

  it("keeps the object order of the values and puts the timestamp last", () => {
    const group = {
      _info: info("SoftTotal"),
      Time: time(),
      Value: arg({ value: 12345, le: 25000 }),
      Total: arg({ arg: "Total", value: 23145, le: 25000 }),
    };

    expect(names(dashboardFields(group))).toEqual(["Value", "Total", "Time"]);
  });

  it("includes every editable setting of the group", () => {
    const group = {
      _info: info("Amount"),
      Time: time(),
      Value: arg({ value: 119 }),
      Control: arg({ arg: "Control", dt: "select", value: "prevent_yellow_face" }),
      Recover: arg({ arg: "Recover", dt: "select", value: "not_in_dormitory" }),
      Oath: arg({ arg: "Oath", dt: "checkbox", value: false }),
    };

    expect(names(dashboardFields(group))).toEqual(["Value", "Control", "Recover", "Oath", "Time"]);
  });

  it("lists every editable arg of a Planner, its Progress and Eta included", () => {
    const group = {
      _info: info("Planner"),
      Time: time(),
      Progress: arg({ arg: "Progress", value: 80 }),
      Eta: arg({ arg: "Eta", dt: "input", value: "01:23:45" }),
      GreenValue: arg({ arg: "GreenValue", value: 8 }),
      GreenTotal: arg({ arg: "GreenTotal", value: 10 }),
      BlueValue: arg({ arg: "BlueValue", value: 22 }),
      BlueTotal: arg({ arg: "BlueTotal", value: 44 }),
    };

    // The plan numbers are editable like any other arg of the group, and the
    // record of a plan is timestamped as well
    expect(names(dashboardFields(group))).toEqual([
      "Progress",
      "Eta",
      "GreenValue",
      "GreenTotal",
      "BlueValue",
      "BlueTotal",
      "Time",
    ]);
  });

  it("skips a read-only arg", () => {
    const group = {
      _info: info("Total"),
      Time: time(),
      Value: arg({ value: 1234, le: 25000 }),
      Total: arg({ arg: "Total", dt: "static", value: 25000 }),
    };

    expect(names(dashboardFields(group))).toEqual(["Value", "Time"]);
  });

  it("returns nothing for a group without an editable arg", () => {
    expect(dashboardFields({ _info: info("Amount") })).toEqual([]);
  });

  it("leaves the timestamp out of a group without a record", () => {
    // A group that only carries computed columns has no record to timestamp
    const group = {
      _info: info("DynamicTotal"),
      Time: time(),
      Total: arg({ arg: "Total", dt: "static", value: 0 }),
    };

    expect(dashboardFields(group)).toEqual([]);
  });

  it("treats a group without `_info` as a non-planner group", () => {
    const group = { Value: arg({ value: 1 }), Time: time() };

    expect(names(dashboardFields(group))).toEqual(["Value", "Time"]);
  });
});

describe("TestDashboardRecordArg", () => {
  it("names the Value of a record group", () => {
    expect(dashboardRecordArg({ Value: arg(), Time: time() })).toBe("Value");
  });

  it("names the Progress of a planner", () => {
    expect(dashboardRecordArg({ Progress: arg({ arg: "Progress" }), Eta: arg({ arg: "Eta" }) })).toBe("Progress");
  });

  it("names nothing for a group without a record", () => {
    expect(dashboardRecordArg({})).toBeUndefined();
    expect(dashboardRecordArg({ Total: arg({ arg: "Total" }) })).toBeUndefined();
  });
});

describe("TestParseDashboardField", () => {
  it("parses an integer within the constraints", () => {
    expect(parseDashboardField("119", arg({ le: 150 }))).toEqual({ value: 119, error: null });
  });

  it("accepts the committed number of the arg input", () => {
    expect(parseDashboardField(119, arg({ le: 150 }))).toEqual({ value: 119, error: null });
  });

  it("reports a value above le", () => {
    expect(parseDashboardField("151", arg({ le: 150 }))).toEqual({
      value: undefined,
      error: t.Input.LessThanOrEqual({ value: 150 }),
    });
  });

  it("reports a value below ge", () => {
    expect(parseDashboardField("-1", arg())).toEqual({
      value: undefined,
      error: t.Input.GreaterThanOrEqual({ value: 0 }),
    });
  });

  it("reports a non integer for input-int", () => {
    expect(parseDashboardField("1.5", arg())).toEqual({ value: undefined, error: t.Input.InvalidInteger() });
    expect(parseDashboardField("abc", arg())).toEqual({ value: undefined, error: t.Input.InvalidInteger() });
  });

  it("parses a float of input-float", () => {
    expect(parseDashboardField("86.54", arg({ dt: "input-float", value: 0.0 }))).toEqual({
      value: 86.54,
      error: null,
    });
  });

  it("checks the value of a datetime", () => {
    expect(parseDashboardField("2026-11-26T08:00:00Z", time())).toEqual({
      value: "2026-11-26T08:00:00Z",
      error: null,
    });
    expect(parseDashboardField("not a time", time())).toEqual({ value: undefined, error: t.Input.InvalidDatetime() });
  });

  it("takes a non-text value as it is", () => {
    // A select commits the option it offers, a checkbox a bool: nothing to parse
    expect(parseDashboardField("prevent_yellow_face", arg({ dt: "select" }))).toEqual({
      value: "prevent_yellow_face",
      error: null,
    });
    expect(parseDashboardField(false, arg({ dt: "checkbox" }))).toEqual({ value: false, error: null });
  });

  it("does not run the pattern of a filter arg as a constraint", () => {
    // `pattern` decodes the items of a filter-regex, it is not a constraint of
    // the value, and applying it would reject every legal item
    const filter = arg({ arg: "Order", dt: "filter-regex", value: ["S9-DR-0.5"], pattern: "(?<series>S\\d)?" });

    expect(parseDashboardField(["S9-DR-0.5"], filter)).toEqual({ value: ["S9-DR-0.5"], error: null });
  });
});

describe("TestSameValue", () => {
  it("treats the typed string as the stored number", () => {
    expect(sameValue("119", 119)).toBe(true);
    expect(sameValue(119, "119")).toBe(true);
    expect(sameValue("119.0", 119)).toBe(true);
  });

  it("tells two values apart", () => {
    expect(sameValue("118", 119)).toBe(false);
    expect(sameValue("abc", 119)).toBe(false);
  });

  it("compares strings and booleans as they are", () => {
    expect(sameValue("auto", "auto")).toBe(true);
    expect(sameValue("auto", "manual")).toBe(false);
    expect(sameValue(false, false)).toBe(true);
  });

  it("never equates a missing value with zero", () => {
    expect(sameValue(null, 0)).toBe(false);
    expect(sameValue(undefined, 0)).toBe(false);
    expect(sameValue(null, null)).toBe(true);
  });
});

describe("TestSameInstant", () => {
  it("compares by instant, not by string", () => {
    expect(sameInstant("2026-11-26T08:00:00Z", "2026-11-26T08:00:00.000Z")).toBe(true);
  });

  it("tells two instants apart", () => {
    expect(sameInstant("2026-11-26T08:00:00Z", "2026-11-26T08:00:01Z")).toBe(false);
  });

  it("falls back to equality for unreadable values", () => {
    expect(sameInstant("unknown", "unknown")).toBe(true);
    expect(sameInstant("unknown", "other")).toBe(false);
  });
});

describe("TestSameField", () => {
  it("compares a datetime by instant", () => {
    expect(sameField(time("2026-11-26T08:00:00.000Z"), time("2026-11-26T08:00:00Z"))).toBe(true);
    expect(sameField(time("2026-11-26T08:00:01Z"), time("2026-11-26T08:00:00Z"))).toBe(false);
  });

  it("compares a number with the text of its input", () => {
    expect(sameField(arg({ value: 119 }), arg({ value: "119" }))).toBe(true);
    expect(sameField(arg({ value: 118 }), arg({ value: 119 }))).toBe(false);
  });

  it("compares the option of a select", () => {
    expect(sameField(arg({ dt: "select", value: "auto" }), arg({ dt: "select", value: "auto" }))).toBe(true);
    expect(sameField(arg({ dt: "select", value: "manual" }), arg({ dt: "select", value: "auto" }))).toBe(false);
  });
});

describe("TestDashboardEditEvents", () => {
  it("builds one event per changed field, in the order of the draft", () => {
    const changes = [
      { data: arg({ arg: "Value", value: 100 }), value: 100 },
      { data: time("2026-11-26T10:00:00.000Z"), value: "2026-11-26T10:00:00.000Z" },
    ];

    expect(dashboardEditEvents(changes)).toEqual([
      { task: "Dashboard", group: "Gems", arg: "Value", value: 100 },
      { task: "Dashboard", group: "Gems", arg: "Time", value: "2026-11-26T10:00:00.000Z" },
    ]);
  });

  it("keeps the (task, group, arg) reference of every field", () => {
    const total = arg({ task: "Main", group: "Emotion1", arg: "Total", value: 40 });

    expect(dashboardEditEvents([{ data: total, value: 40 }])).toEqual([
      { task: "Main", group: "Emotion1", arg: "Total", value: 40 },
    ]);
  });
});

/**
 * Unit tests for LineSplitter.
 */
import { describe, expect, it } from "vitest";
import { LineSplitter } from "./line-splitter";

const collect = (chunks: string[]): string[] => {
  const lines: string[] = [];
  const splitter = new LineSplitter((line) => lines.push(line));
  for (const chunk of chunks) {
    splitter.push(chunk);
  }
  return lines;
};

describe("TestLineSplitter", () => {
  it("splits a single complete line in one push (fast path)", () => {
    expect(collect(["hello\n"])).toEqual(["hello"]);
  });

  it("splits consecutive complete lines (fast path per push)", () => {
    expect(collect(["a\n", "b\n", "c\n"])).toEqual(["a", "b", "c"]);
  });

  it("joins a line split across pushes", () => {
    expect(collect(["hel", "lo\n"])).toEqual(["hello"]);
  });

  it("splits multiple lines in one push", () => {
    expect(collect(["a\nb\nc\n"])).toEqual(["a", "b", "c"]);
  });

  it("splits multiple lines without a trailing newline", () => {
    expect(collect(["a\nb"])).toEqual(["a"]);
    expect(collect(["a\nb", "\n"])).toEqual(["a", "b"]);
  });

  it("buffers a partial line without newline", () => {
    expect(collect(["hello"])).toEqual([]);
  });

  it("completes a partial line on a later push", () => {
    expect(collect(["hello", " world\n"])).toEqual(["hello world"]);
  });

  it("handles a complete line followed by a partial buffer", () => {
    expect(collect(["a\nbc"])).toEqual(["a"]);
    expect(collect(["a\nbc", "d\n"])).toEqual(["a", "bcd"]);
  });

  it("ignores an empty push", () => {
    expect(collect([""])).toEqual([]);
  });

  it("emits an empty line", () => {
    expect(collect(["\n"])).toEqual([""]);
    expect(collect(["\r\n"])).toEqual([""]);
  });

  it("strips a CRLF line ending", () => {
    // Windows pipes deliver CRLF; the trailing "\r" is part of the line
    // ending, not the line content.
    expect(collect(["a\r\n"])).toEqual(["a"]);
    expect(collect(["a\r\n", "b\n"])).toEqual(["a", "b"]);
  });

  it("strips a CRLF split across pushes", () => {
    expect(collect(["hel", "lo\r\n"])).toEqual(["hello"]);
    expect(collect(["a\r", "b\n"])).toEqual(["a\rb"]);
  });

  it("keeps a carriage return inside the line", () => {
    // Only the line-ending "\r" of a CRLF line is stripped; a "\r"
    // anywhere else in the line is raw content.
    expect(collect(["a\rb\n"])).toEqual(["a\rb"]);
  });
});

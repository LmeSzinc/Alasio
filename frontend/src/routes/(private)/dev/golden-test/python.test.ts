import { describe, expect, it } from "vitest";
import { tokenizePython } from "./python";

/** Join the spans of a line back into the text of the line */
function text(spans: { text: string }[]): string {
  return spans.map((span) => span.text).join("");
}

/** The class of the span that holds some text, for a compact assertion */
function classOf(source: string, needle: string): string {
  const spans = tokenizePython(source).flat();
  const exact = spans.find((span) => span.text === needle);
  if (exact !== undefined) return exact.cls;
  // Adjacent plain tokens are one span, e.g. "fleet = 1"
  const holding = spans.find((span) => span.text.includes(needle));
  if (holding !== undefined) return holding.cls;
  throw new Error(`"${needle}" is not a span of the source`);
}

describe("TestTokenizePythonLines", () => {
  it("keeps one entry per line of the file", () => {
    const source = "a = 1\nb = 2\nc = 3\n";
    expect(tokenizePython(source).map(text)).toEqual(["a = 1", "b = 2", "c = 3"]);
  });

  it("drops the empty line after a trailing newline", () => {
    expect(tokenizePython("a = 1\n")).toHaveLength(1);
    expect(tokenizePython("a = 1")).toHaveLength(1);
  });

  it("keeps the blank lines of the file", () => {
    expect(tokenizePython("a = 1\n\nb = 2\n").map(text)).toEqual(["a = 1", "", "b = 2"]);
    expect(tokenizePython("\n\n")).toHaveLength(2);
  });

  it("returns no line for an empty file", () => {
    expect(tokenizePython("")).toEqual([]);
  });

  it("keeps a triple quoted string on its own lines", () => {
    const source = 'def f():\n    """\n    Doc line\n    """\n    return 1\n';
    const lines = tokenizePython(source);
    expect(lines.map(text)).toEqual(["def f():", '    """', "    Doc line", '    """', "    return 1"]);
    // the doc string is one string span spread over the four lines of it
    expect(lines[1][1]).toEqual({ text: '"""', cls: "string" });
    expect(lines[2][0]).toEqual({ text: "    Doc line", cls: "string" });
    expect(lines[3][0]).toEqual({ text: '    """', cls: "string" });
  });

  it("keeps the text after a triple quoted string on its line", () => {
    const lines = tokenizePython('x = """a\nb""" + 1\n');
    expect(lines.map(text)).toEqual(['x = """a', 'b""" + 1']);
    expect(lines[1].map((span) => span.cls)).toEqual(["string", "", "number"]);
  });

  it("joins every character of a line into its spans", () => {
    const source = "def f(x):\n    return {'a': [1, 2.5]}  # note\n";
    for (const line of tokenizePython(source)) {
      expect(line.length).toBeGreaterThan(0);
    }
    expect(tokenizePython(source).map(text)).toEqual(source.split("\n").slice(0, -1));
  });
});

describe("TestTokenizePythonNames", () => {
  it("tags the keywords of the flow", () => {
    expect(classOf("if x:\n", "if")).toBe("keyword");
    expect(classOf("return 1\n", "return")).toBe("keyword");
    expect(classOf("async def f():\n", "async")).toBe("keyword");
  });

  it("tags True, False and None as constants", () => {
    expect(classOf("x = True\n", "True")).toBe("constant");
    expect(classOf("x = False\n", "False")).toBe("constant");
    expect(classOf("x = None\n", "None")).toBe("constant");
  });

  it("tags self and cls as constants", () => {
    expect(classOf("def f(self):\n", "self")).toBe("constant");
    expect(classOf("def f(cls):\n", "cls")).toBe("constant");
  });

  it("tags a builtin", () => {
    expect(classOf("print(len(x))\n", "print")).toBe("builtin");
    expect(classOf("print(len(x))\n", "len")).toBe("builtin");
  });

  it("tags the name of a def and of a class", () => {
    expect(classOf("def detect_fleet_bar(self):\n", "detect_fleet_bar")).toBe("defname");
    expect(classOf("class TestFleetBarNotExpanded:\n", "TestFleetBarNotExpanded")).toBe("classname");
    expect(classOf("    async def test_a(self):\n", "test_a")).toBe("defname");
  });

  it("keeps a name that is not a keyword plain", () => {
    expect(classOf("fleet = 1\n", "fleet")).toBe("");
  });

  it("does not take a name after def on the next line", () => {
    // A def without a name is a broken file, the name of the next line must not
    // become the name of the def
    expect(classOf("def\nx = 1\n", "x")).toBe("");
  });
});

describe("TestTokenizePythonLiterals", () => {
  it("tags a comment up to the end of the line", () => {
    const lines = tokenizePython("x = 1  # a note\n");
    expect(lines[0].at(-1)).toEqual({ text: "# a note", cls: "comment" });
  });

  it("does not read a # inside a string as a comment", () => {
    const lines = tokenizePython('x = "# not a comment"\n');
    expect(lines[0].some((span) => span.cls === "comment")).toBe(false);
    expect(classOf('x = "# not a comment"\n', '"# not a comment"')).toBe("string");
  });

  it("tags a single quoted string", () => {
    expect(classOf("x = 'a'\n", "'a'")).toBe("string");
    expect(classOf('x = "a"\n', '"a"')).toBe("string");
  });

  it("tags a prefixed string", () => {
    expect(classOf('x = f"{a}"\n', 'f"{a}"')).toBe("string");
    expect(classOf("x = rb'\\x00'\n", "rb'\\x00'")).toBe("string");
    expect(classOf("x = u'a'\n", "u'a'")).toBe("string");
  });

  it("keeps the quote escaped by a backslash", () => {
    expect(classOf('x = "a\\"b"\n', '"a\\"b"')).toBe("string");
    expect(classOf("x = 'a\\'b'\n", "'a\\'b'")).toBe("string");
  });

  it("does not let a single quoted string cross the end of its line", () => {
    // A broken file is still displayed: the literal ends with the line
    const lines = tokenizePython("x = 'a\ny = 1\n");
    expect(lines.map(text)).toEqual(["x = 'a", "y = 1"]);
    expect(lines[0][1]).toEqual({ text: "'a", cls: "string" });
  });

  it("tags a number", () => {
    for (const literal of ["1", "1_000", "2.5", "0x1f", "0b1010", "1e-3", "3j"]) {
      expect(classOf(`x = ${literal}\n`, literal)).toBe("number");
    }
  });
});

describe("TestTokenizePythonDecorator", () => {
  it("tags a decorator at the start of a line", () => {
    expect(classOf('@image("a.webp")\n', "@image")).toBe("decorator");
    expect(classOf("    @alasio.testing.image\n", "@alasio.testing.image")).toBe("decorator");
  });

  it("does not tag a matrix product as a decorator", () => {
    expect(classOf("x = a @ b\n", "@")).toBe("");
  });

  it("tags a decorator after a blank line", () => {
    expect(classOf("\n@image('a')\n", "@image")).toBe("decorator");
  });
});

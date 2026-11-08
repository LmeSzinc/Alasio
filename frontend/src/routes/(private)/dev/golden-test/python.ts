/**
 * A small Python tokenizer for the golden test review page.
 *
 * The page shows the source of a test file next to the screenshots it binds, so
 * the code has to stay readable at a glance: keywords, strings, comments and
 * numbers are colored. This is a scanner, not a parser: it walks the source
 * once and emits spans, which is enough for a source view and keeps the page
 * free of a highlighting engine and its grammars (the frontend is delivered
 * inside the desktop client, every kilobyte of it is shipped).
 *
 * The one structural rule that matters is the line count: the rows of the page
 * are the lines of the file, 1:1, so the output always holds exactly one entry
 * per line of the source, even when a token (a triple quoted string) spans
 * several of them.
 */

/**
 * One run of characters of a line, tagged with the class of its color.
 *
 * An empty `cls` is plain code. The values are the keys of `SPAN_CLASS` in
 * SourceView.svelte.
 */
export type Span = {
  text: string;
  cls: string;
};

/** Words that are always a keyword */
const KEYWORDS = new Set([
  "False",
  "None",
  "True",
  "and",
  "as",
  "assert",
  "async",
  "await",
  "break",
  "case",
  "class",
  "continue",
  "def",
  "del",
  "elif",
  "else",
  "except",
  "finally",
  "for",
  "from",
  "global",
  "if",
  "import",
  "in",
  "is",
  "lambda",
  "match",
  "nonlocal",
  "not",
  "or",
  "pass",
  "raise",
  "return",
  "try",
  "while",
  "with",
  "yield",
]);

/** Keywords that read as a value, colored apart from the flow keywords */
const CONSTANTS = new Set(["False", "None", "True"]);

/** Builtins a test file actually uses; coloring every builtin is noise */
const BUILTINS = new Set([
  "Exception",
  "TypeError",
  "ValueError",
  "any",
  "all",
  "bool",
  "dict",
  "enumerate",
  "float",
  "frozenset",
  "getattr",
  "hasattr",
  "int",
  "isinstance",
  "len",
  "list",
  "max",
  "min",
  "print",
  "range",
  "repr",
  "reversed",
  "round",
  "set",
  "setattr",
  "sorted",
  "str",
  "sum",
  "super",
  "tuple",
  "type",
  "zip",
]);

/** The instance parameter of a method, colored like a constant */
const SELF = new Set(["self", "cls"]);

const IDENT_RE = /^[A-Za-z_\u00a0-\uffff][A-Za-z0-9_\u00a0-\uffff]*/;
const NUMBER_RE =
  /^(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?)[jJ]?/;
// A string prefix directly followed by its quote, e.g. f", rb', u"""
const STRING_PREFIX_RE = /^[rRbBuUfF]{1,2}('''|"""|'|")/;
const QUOTES = new Set(["'", '"']);

/**
 * Tokenize Python source into the lines of the page.
 *
 * @param source The full source of the file
 * @returns One entry per line of `source`, each a list of colored spans
 */
export function tokenizePython(source: string): Span[][] {
  const lines: Span[][] = [[]];
  // The last token that was not whitespace, to tag the name after def / class
  let previous = "";

  const push = (text: string, cls: string) => {
    if (!text) return;
    const parts = text.split("\n");
    for (let i = 0; i < parts.length; i++) {
      if (i > 0) lines.push([]);
      const part = parts[i];
      if (!part) continue;
      const line = lines[lines.length - 1];
      const last = line[line.length - 1];
      if (last !== undefined && last.cls === cls) {
        last.text += part;
      } else {
        line.push({ text: part, cls });
      }
    }
    // Indentation keeps the previous token, a newline starts a new one
    if (text.trim() !== "") {
      previous = text.trim();
    } else if (text.includes("\n")) {
      previous = "";
    }
  };

  let i = 0;
  const n = source.length;
  while (i < n) {
    const char = source[i];

    // Whitespace, including the newline that ends a line
    if (char === " " || char === "\t" || char === "\r" || char === "\n") {
      let end = i + 1;
      while (end < n && " \t\r\n".includes(source[end])) end++;
      push(source.slice(i, end), "");
      i = end;
      continue;
    }

    // Comment, up to the end of the line
    if (char === "#") {
      let end = source.indexOf("\n", i);
      if (end < 0) end = n;
      push(source.slice(i, end), "comment");
      i = end;
      continue;
    }

    // Decorator: @name or @a.b, at the start of a line
    if (char === "@" && previous === "") {
      let end = i + 1;
      while (end < n && /[A-Za-z0-9_.]/.test(source[end])) end++;
      push(source.slice(i, end), "decorator");
      i = end;
      continue;
    }

    // String literal, with an optional prefix (r, b, f, u and their pairs)
    const prefix = STRING_PREFIX_RE.exec(source.slice(i));
    if (QUOTES.has(char) || prefix !== null) {
      const end = scanString(source, prefix === null ? i : i + prefix[1].length);
      push(source.slice(i, end), "string");
      i = end;
      continue;
    }

    // Number
    if ((char >= "0" && char <= "9") || (char === "." && /\d/.test(source[i + 1] ?? ""))) {
      const match = NUMBER_RE.exec(source.slice(i));
      const end = match === null ? i + 1 : i + match[0].length;
      push(source.slice(i, end), "number");
      i = end;
      continue;
    }

    // Name: a keyword, a builtin, the name of a def / class, or plain code
    const name = IDENT_RE.exec(source.slice(i));
    if (name !== null) {
      const text = name[0];
      let cls = "";
      if (CONSTANTS.has(text)) {
        cls = "constant";
      } else if (KEYWORDS.has(text)) {
        cls = "keyword";
      } else if (previous === "def" || previous === "class") {
        cls = previous === "def" ? "defname" : "classname";
      } else if (SELF.has(text)) {
        cls = "constant";
      } else if (BUILTINS.has(text)) {
        cls = "builtin";
      }
      push(text, cls);
      i += text.length;
      continue;
    }

    push(char, "");
    i += 1;
  }

  // A file ending with a newline has no line after it
  if (lines.length > 1 && lines[lines.length - 1].length === 0) lines.pop();
  if (source === "") return [];
  return lines;
}

/**
 * Find the end of the string literal that starts at a quote.
 *
 * A backslash protects the character after it in every kind of string: in a raw
 * string it is not an escape for the value, but the quote after it still does
 * not end the literal.
 *
 * @param source The full source
 * @param start Index of the opening quote
 * @returns Index just past the closing quote, the end of the line if the
 *     literal is unterminated (a broken file is still displayed)
 */
function scanString(source: string, start: number): number {
  const quote = source[start];
  const triple = source.startsWith(quote.repeat(3), start);
  const delimiter = triple ? quote.repeat(3) : quote;
  let i = start + delimiter.length;
  const n = source.length;

  while (i < n) {
    if (source[i] === "\\") {
      i += 2;
      continue;
    }
    if (!triple && source[i] === "\n") {
      // A single quoted literal never crosses the end of its line
      return i;
    }
    if (source.startsWith(delimiter, i)) {
      return i + delimiter.length;
    }
    i += 1;
  }
  return n;
}

// Validation of the drag region the embedded frontend reports.
//
// The upstream side is untrusted: the frontend runs in an iframe from the
// backend origin and its message is what the title bar turns into a CSS inset,
// so every value is checked here before it reaches a style attribute. Numbers
// are pixels; strings are CSS expressions the frontend composes for the one
// case a constant cannot express (the sheet panel is a percentage of the
// window). Anything else is refused.
//
// Pure module on purpose: it carries no runes, so it is unit tested in the node
// environment of this project (see vitest.config.mts).

/** A CSS length: a number of pixels, or an expression the browser evaluates */
export type CssLength = number | string;

export interface DragRegion {
  height: CssLength;
  left: CssLength;
  right: CssLength;
}

const MAX_EXPRESSION_LENGTH = 200;
// Units, numbers and the arithmetic of a CSS calc(); no identifiers other than
// the units themselves reach the browser through this path.
const EXPRESSION_CHARS = /^[0-9a-z.\s%+\-*/(),]+$/i;
const EXPRESSION_START = /^(calc|min|max|clamp)\(/i;
// Named values that would let the reported string reach outside its own value:
// var() pulls in a custom property, url()/image() fetch, env()/attr() read the
// document, expression() is legacy script.
const EXPRESSION_FORBIDDEN = /(url|var|expression|import|image|env|attr)/i;
// A comment is not part of the arithmetic the frontend composes; refusing it
// keeps the accepted language exactly the expressions this app generates.
const EXPRESSION_COMMENT = /\/\*|\*\//;

/**
 * Validates one inset of a reported region.
 *
 * Args:
 *   value: Reported inset
 *   allowZero: Whether 0 is a valid value
 *
 * Returns:
 *   The inset, or `null` when it must be refused
 */
export function sanitizeLength(value: unknown, allowZero: boolean): CssLength | null {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    if (allowZero ? value < 0 : value <= 0) return null;
    return value;
  }
  if (typeof value !== "string") return null;
  if (value.length === 0 || value.length > MAX_EXPRESSION_LENGTH) return null;
  if (!EXPRESSION_CHARS.test(value)) return null;
  if (!EXPRESSION_START.test(value)) return null;
  if (EXPRESSION_FORBIDDEN.test(value)) return null;
  if (EXPRESSION_COMMENT.test(value)) return null;
  return value;
}

/**
 * Validates a reported region.
 *
 * Args:
 *   raw: Value of the `alasio:drag-region` message
 *
 * Returns:
 *   The region, or `null` when the message must be dropped
 */
export function sanitizeRegion(raw: unknown): DragRegion | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const height = sanitizeLength(record.height, false);
  const left = sanitizeLength(record.left, true);
  const right = sanitizeLength(record.right, true);
  if (height === null || left === null || right === null) return null;
  return { height, left, right };
}

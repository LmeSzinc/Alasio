/**
 * The layout of a source view: where the screenshots sit in the code.
 *
 * The middle column shows the lines of the file and the right column shows a
 * screenshot next to the test function it belongs to. The two columns are laid
 * out as one stack of segments, one segment per image: the code of a segment
 * starts at the line of the image, so the image is aligned with the first
 * decorator of its test and never overlaps the code of the next one.
 *
 * Segments are line ranges, not copies of the lines: the page slices the
 * highlighted source with them, so a line is highlighted once for the whole
 * file and the line numbers stay the numbers of the file.
 */
import type { UnittestImage } from "./types.js";

/** An image with every field filled, as the view reads it */
export type NormalizedImage = {
  class_name: string;
  func_name: string;
  start_line: number;
  end_line: number;
  image: string;
  error: string;
};

/** A run of source lines and the image that starts with it */
export type SourceSegment = {
  /** First line, 1-based */
  start: number;
  /** Last line, 1-based and inclusive */
  end: number;
  /** The image of the segment, null for a segment that is pure code */
  image: NormalizedImage | null;
};

/**
 * Fill the default values of the images the backend sent.
 *
 * @param images The image bindings of the file, in source order
 * @returns The bindings with every field present, in source order
 */
export function normalizeImages(images: UnittestImage[] | undefined): NormalizedImage[] {
  return (images ?? []).map((image) => ({
    class_name: image.class_name ?? "",
    func_name: image.func_name ?? "",
    start_line: image.start_line ?? 1,
    end_line: image.end_line ?? 1,
    image: image.image ?? "",
    error: image.error ?? "",
  }));
}

/**
 * Split the lines of a file into the segments of the source view.
 *
 * The first segment is the code above the first image and has no image; every
 * image then starts a segment that runs to the line before the next image. A
 * file without images is one plain segment. An image line is clamped into the
 * file, and two images on the same line keep the first one: the decorator lines
 * of a broken file may point anywhere, the view must still lay out.
 *
 * @param lineCount Number of lines of the file
 * @param images The image bindings, normalized
 * @returns The segments, in line order, covering every line exactly once
 */
export function buildSegments(lineCount: number, images: NormalizedImage[]): SourceSegment[] {
  if (lineCount <= 0) return [];

  const anchors: { line: number; image: NormalizedImage }[] = [];
  const clamped = images.map((image) => ({
    line: Math.min(Math.max(image.start_line, 1), lineCount),
    image,
  }));
  // The payload is in source order, the view does not depend on it
  clamped.sort((a, b) => a.line - b.line);
  for (const { line, image } of clamped) {
    const previous = anchors[anchors.length - 1];
    if (previous !== undefined && line === previous.line) continue;
    anchors.push({ line, image });
  }

  const segments: SourceSegment[] = [];
  let cursor = 1;
  for (let i = 0; i < anchors.length; i++) {
    const { line, image } = anchors[i];
    const next = i + 1 < anchors.length ? anchors[i + 1].line : lineCount + 1;
    if (line > cursor) {
      segments.push({ start: cursor, end: line - 1, image: null });
    }
    segments.push({ start: line, end: next - 1, image });
    cursor = next;
  }
  if (cursor <= lineCount) {
    segments.push({ start: cursor, end: lineCount, image: null });
  }
  return segments;
}

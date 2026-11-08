import { describe, expect, it } from "vitest";
import { type NormalizedImage, buildSegments, normalizeImages } from "./segments";
import { imageUrl } from "./types";

/** An image binding of the given line, the other fields at their defaults */
function imageAt(start: number, path = "a.webp"): NormalizedImage {
  return {
    class_name: "",
    func_name: `test_${start}`,
    start_line: start,
    end_line: start + 3,
    image: path,
    error: "",
  };
}

/** The segments as (start, end, image line or 0) triples, for a compact assertion */
function shape(segments: ReturnType<typeof buildSegments>): [number, number, number][] {
  return segments.map((segment) => [segment.start, segment.end, segment.image?.start_line ?? 0]);
}

describe("TestNormalizeImages", () => {
  it("fills every field of an omitted payload", () => {
    expect(normalizeImages([{}])).toEqual([
      { class_name: "", func_name: "", start_line: 1, end_line: 1, image: "", error: "" },
    ]);
  });

  it("keeps the values that are sent", () => {
    expect(
      normalizeImages([
        {
          class_name: "TestA",
          func_name: "test_b",
          start_line: 4,
          end_line: 9,
          image: "map/a.webp",
          error: "no such image",
        },
      ]),
    ).toEqual([
      {
        class_name: "TestA",
        func_name: "test_b",
        start_line: 4,
        end_line: 9,
        image: "map/a.webp",
        error: "no such image",
      },
    ]);
  });

  it("reads an absent list as no image", () => {
    expect(normalizeImages(undefined)).toEqual([]);
  });
});

describe("TestBuildSegments", () => {
  it("returns no segment for an empty file", () => {
    expect(buildSegments(0, [])).toEqual([]);
  });

  it("returns one plain segment for a file without images", () => {
    expect(shape(buildSegments(10, []))).toEqual([[1, 10, 0]]);
  });

  it("puts the code above the first image in a segment of its own", () => {
    expect(shape(buildSegments(20, [imageAt(8)]))).toEqual([
      [1, 7, 0],
      [8, 20, 8],
    ]);
  });

  it("ends a segment at the line before the next image", () => {
    expect(shape(buildSegments(30, [imageAt(5), imageAt(12), imageAt(20)]))).toEqual([
      [1, 4, 0],
      [5, 11, 5],
      [12, 19, 12],
      [20, 30, 20],
    ]);
  });

  it("starts with the image when it is on the first line", () => {
    expect(shape(buildSegments(6, [imageAt(1)]))).toEqual([[1, 6, 1]]);
  });

  it("keeps the code after the last image in a segment of its own", () => {
    expect(shape(buildSegments(9, [imageAt(3)]))).toEqual([
      [1, 2, 0],
      [3, 9, 3],
    ]);
  });

  it("clamps an image past the end of the file to the last line", () => {
    expect(shape(buildSegments(5, [imageAt(40)]))).toEqual([
      [1, 4, 0],
      [5, 5, 40],
    ]);
  });

  it("clamps a line of 0 or a negative one to the first line", () => {
    expect(shape(buildSegments(4, [imageAt(0)]))).toEqual([[1, 4, 0]]);
    expect(shape(buildSegments(4, [imageAt(-3, "b.webp")]))).toEqual([[1, 4, -3]]);
  });

  it("keeps the first of two images on the same line", () => {
    const segments = buildSegments(10, [imageAt(4, "first.webp"), imageAt(4, "second.webp")]);
    expect(shape(segments)).toEqual([
      [1, 3, 0],
      [4, 10, 4],
    ]);
    expect(segments[1].image?.image).toBe("first.webp");
  });

  it("keeps the images in line order whatever the order of the payload", () => {
    const segments = buildSegments(20, [imageAt(12, "b.webp"), imageAt(5, "a.webp")]);
    expect(shape(segments)).toEqual([
      [1, 4, 0],
      [5, 11, 5],
      [12, 20, 12],
    ]);
    expect(segments[1].image?.image).toBe("a.webp");
  });

  it("covers every line exactly once", () => {
    const lineCount = 37;
    const segments = buildSegments(lineCount, [imageAt(1), imageAt(9), imageAt(9), imageAt(10), imageAt(30)]);
    const lines = segments.flatMap((segment) =>
      Array.from({ length: segment.end - segment.start + 1 }, (_, i) => segment.start + i),
    );
    expect(lines).toEqual(Array.from({ length: lineCount }, (_, i) => i + 1));
  });
});

describe("TestImageUrl", () => {
  it("builds the route of the image", () => {
    expect(imageUrl("example_mod", "map/fleet_bar/not_expanded.webp")).toBe(
      "/api/golden_test/image?mod=example_mod&image_path=map%2Ffleet_bar%2Fnot_expanded.webp",
    );
  });

  it("encodes the spaces and the non-ascii characters of a path", () => {
    expect(imageUrl("mod", "map/a b#c.webp")).toBe("/api/golden_test/image?mod=mod&image_path=map%2Fa+b%23c.webp");
    expect(imageUrl("mod", "地图/a.webp")).toBe(
      "/api/golden_test/image?mod=mod&image_path=%E5%9C%B0%E5%9B%BE%2Fa.webp",
    );
  });
});

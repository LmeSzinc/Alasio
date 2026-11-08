/**
 * The wire shapes of the golden test review topics, and the URL of a screenshot.
 *
 * Every field of the backend models is optional here: the structs use
 * `omit_defaults`, so a zero value is absent from the payload rather than
 * null, and the page normalizes what it reads.
 */

/** One child of a folder of the tree */
export type GoldenTestEntry = {
  name: string;
  /** Path relative to `tests/`, POSIX separators */
  path: string;
  is_dir?: boolean;
};

/** `GoldenTestTree`: the folders the page has subscribed so far */
export type GoldenTestTreeData = {
  /** The browsed mod, empty before the page selects one */
  mod_name?: string;
  /** Folder path relative to `tests/` -> its immediate children, '' is the root */
  dirs?: Record<string, GoldenTestEntry[]>;
};

/** An image bound to a test function by `@image` */
export type UnittestImage = {
  /** Enclosing class name, empty for a module level test */
  class_name?: string;
  func_name?: string;
  /** First decorator line, 1-based */
  start_line?: number;
  /** Last line of the function, 1-based and inclusive */
  end_line?: number;
  /** Path relative to the collection folder, empty when the binding is invalid */
  image?: string;
  /** Function level error, empty when the binding is valid */
  error?: string;
};

/** `GoldenTestFile`: the selected test file */
export type GoldenTestFileData = {
  mod_name?: string;
  /** Path relative to `tests/` */
  file?: string;
  /** The full source, highlighted by the page */
  source?: string;
  images?: UnittestImage[];
  /** File level errors, e.g. a syntax error */
  errors?: string[];
};

/** A mod of the `ModList` topic, as the mod selector reads it */
export type ModOption = {
  value: string;
  label: string;
};

/**
 * Build the URL of a screenshot.
 *
 * The page and the backend are served from the same origin and the desktop
 * client injects the electron token into every request of its window, so the
 * `<img>` authenticates like the rest of the page does.
 *
 * @param mod Name of the mod
 * @param imagePath Path relative to the collection folder, as `@image` wrote it
 * @returns The URL of the image route
 */
export function imageUrl(mod: string, imagePath: string): string {
  const params = new URLSearchParams({ mod, image_path: imagePath });
  return `/api/golden_test/image?${params.toString()}`;
}

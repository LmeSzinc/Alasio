/**
 * Filter files based on accept attribute
 * Supports MIME types (image/*, image/png) and file extensions (.jpg, .png)
 */
export function filterFilesByAccept(files: File[] | FileList, accept: string): File[] {
  const acceptTypes = accept.split(",").map((type) => type.trim());
  const filesArray = Array.from(files);

  return filesArray.filter((file) => {
    return acceptTypes.some((acceptType) => {
      // Handle MIME types (e.g., "image/*", "image/png")
      if (acceptType.includes("/")) {
        if (acceptType.endsWith("/*")) {
          const category = acceptType.split("/")[0];
          return file.type.startsWith(category + "/");
        }
        return file.type === acceptType;
      }

      // Handle file extensions (e.g., ".png", ".jpg")
      if (acceptType.startsWith(".")) {
        return file.name.toLowerCase().endsWith(acceptType.toLowerCase());
      }

      return false;
    });
  });
}

/**
 * Extract files from clipboard items
 */
export function extractFilesFromClipboard(items: DataTransferItemList): File[] {
  const files: File[] = [];

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind === "file") {
      const file = item.getAsFile();
      if (file) {
        files.push(file);
      }
    }
  }

  return files;
}

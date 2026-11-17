const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const MAX_SOURCE_BYTES = 25 * 1024 * 1024;
const MAX_IMAGE_EDGE = 2048;

const DIRECT_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const HEIC_IMAGE_TYPES = new Set(["image/heic", "image/heif"]);

function extensionOf(file: File): string {
  return file.name.split(".").at(-1)?.toLowerCase() ?? "";
}

function inferredType(file: File): string {
  const declaredType = file.type.toLowerCase();
  if (declaredType === "image/jpg" || declaredType === "image/pjpeg") return "image/jpeg";
  if (declaredType === "image/x-png") return "image/png";
  if (declaredType && declaredType !== "application/octet-stream") return declaredType;
  const extension = extensionOf(file);
  if (extension === "jpg" || extension === "jpeg") return "image/jpeg";
  if (extension === "png") return "image/png";
  if (extension === "webp") return "image/webp";
  if (extension === "heic") return "image/heic";
  if (extension === "heif") return "image/heif";
  return "";
}

function isHeic(file: File): boolean {
  const type = inferredType(file);
  return HEIC_IMAGE_TYPES.has(type) || ["heic", "heif"].includes(extensionOf(file));
}

function jpegName(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "") || "photo";
  return `${stem}.jpg`;
}

async function loadImage(blob: Blob): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.decoding = "async";
    image.src = url;
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function canvasBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("image_conversion_failed"))),
      "image/jpeg",
      quality,
    );
  });
}

async function resizeToJpeg(blob: Blob, filename: string): Promise<File> {
  const image = await loadImage(blob);
  const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext("2d");
  if (!context) throw new Error("image_conversion_failed");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);

  let converted = await canvasBlob(canvas, 0.86);
  if (converted.size > MAX_UPLOAD_BYTES) converted = await canvasBlob(canvas, 0.68);
  if (converted.size > MAX_UPLOAD_BYTES) throw new Error("image_too_large");
  return new File([converted], jpegName(filename), {
    type: "image/jpeg",
    lastModified: Date.now(),
  });
}

export async function prepareImageForUpload(file: File): Promise<File> {
  if (file.size > MAX_SOURCE_BYTES) throw new Error("image_source_too_large");

  if (isHeic(file)) {
    try {
      const { default: heic2any } = await import("heic2any");
      const result = await heic2any({ blob: file, toType: "image/jpeg", quality: 0.9 });
      const converted = Array.isArray(result) ? result[0] : result;
      if (!converted) throw new Error("image_conversion_failed");
      return resizeToJpeg(converted, file.name);
    } catch (error) {
      throw new Error("heic_conversion_failed", { cause: error });
    }
  }

  const type = inferredType(file);
  if (!DIRECT_IMAGE_TYPES.has(type)) throw new Error("image_type_not_supported");
  const typedFile = file.type
    ? file
    : new File([file], file.name, { type, lastModified: file.lastModified });
  if (typedFile.size <= MAX_UPLOAD_BYTES) return typedFile;

  try {
    return await resizeToJpeg(typedFile, typedFile.name);
  } catch (error) {
    if (error instanceof Error && error.message === "image_too_large") throw error;
    throw new Error("image_conversion_failed", { cause: error });
  }
}

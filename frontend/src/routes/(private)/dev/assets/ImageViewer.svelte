<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { Button } from "$lib/components/ui/button";
  import { RefreshCcw } from "@lucide/svelte";
  import { cn } from "$lib/utils";

  // --- Component Props ---
  let {
    src,
    class: className = "",
    initScale = 0.9,
    minScale = 0.5,
    maxScale = 64,
    zoomFactor = 1.2,
    pixelGridThreshold = 6,
  } = $props<{
    src?: string | null;
    class?: string;
    initScale?: number;
    minScale?: number;
    maxScale?: number;
    zoomFactor?: number;
    pixelGridThreshold?: number;
  }>();

  // --- Reactive State ---
  let scale = $state(initScale);
  let translateX = $state(0);
  let translateY = $state(0);
  let isDragging = $state(false);
  let isSpacePressed = $state(false);

  // --- Canvas specific state ---
  let containerEl: HTMLDivElement | null = $state(null);
  let imageCanvasEl: HTMLCanvasElement | null = $state(null);
  let gridCanvasEl: HTMLCanvasElement | null = $state(null);
  let ctxImage: CanvasRenderingContext2D | null = $state(null);
  let ctxGrid: CanvasRenderingContext2D | null = $state(null);
  let image: HTMLImageElement | null = $state(null);
  let imageLayout = $state({ x: 0, y: 0, width: 0, height: 0 }); // Stores object-contain layout

  let startDrag = { x: 0, y: 0, translateX: 0, translateY: 0 };

  // --- Helper function to calculate centering offset ---
  function getCenteringOffset(): { x: number; y: number } {
    if (!containerEl || !imageLayout.width || !imageLayout.height) {
      return { x: 0, y: 0 };
    }

    const rect = containerEl.getBoundingClientRect();
    const scaledImageWidth = imageLayout.width * scale;
    const scaledImageHeight = imageLayout.height * scale;

    // Only center if image is smaller than viewport
    let offsetX = 0;
    let offsetY = 0;

    if (scaledImageWidth < rect.width) {
      // Calculate centering offset for X axis
      const imageLeftInViewport = imageLayout.x * scale + translateX;
      const imageRightInViewport = imageLeftInViewport + scaledImageWidth;
      const viewportCenterX = rect.width / 2;
      const imageCenterX = (imageLeftInViewport + imageRightInViewport) / 2;
      offsetX = viewportCenterX - imageCenterX;
    }

    if (scaledImageHeight < rect.height) {
      // Calculate centering offset for Y axis
      const imageTopInViewport = imageLayout.y * scale + translateY;
      const imageBottomInViewport = imageTopInViewport + scaledImageHeight;
      const viewportCenterY = rect.height / 2;
      const imageCenterY = (imageTopInViewport + imageBottomInViewport) / 2;
      offsetY = viewportCenterY - imageCenterY;
    }

    return { x: offsetX, y: offsetY };
  }

  // --- Core Rendering Logic ---
  function render() {
    if (!ctxImage || !ctxGrid || !image || !containerEl) return;

    const canvasWidth = gridCanvasEl!.width;
    const canvasHeight = gridCanvasEl!.height;
    const dpr = window.devicePixelRatio || 1;

    // Clear both canvases
    ctxImage.clearRect(0, 0, canvasWidth, canvasHeight);
    ctxGrid.clearRect(0, 0, canvasWidth, canvasHeight);

    // Get centering offset
    const centerOffset = getCenteringOffset();
    const finalTranslateX = translateX + centerOffset.x;
    const finalTranslateY = translateY + centerOffset.y;

    // --- Render Image Canvas ---
    ctxImage.save();
    ctxImage.translate(finalTranslateX * dpr, finalTranslateY * dpr);
    ctxImage.scale(scale, scale);

    // Add shadow to the image for better visibility
    ctxImage.shadowColor = "rgba(0, 0, 0, 0.7)";
    ctxImage.shadowBlur = 20 / scale; // Scale shadow blur inversely with zoom
    ctxImage.shadowOffsetX = 0;
    ctxImage.shadowOffsetY = 5;

    // Disable image smoothing when zoomed in for crisp pixels
    ctxImage.imageSmoothingEnabled = scale < pixelGridThreshold;
    ctxImage.drawImage(image, imageLayout.x, imageLayout.y, imageLayout.width, imageLayout.height);
    ctxImage.restore();

    // --- Render Grid Canvas ---
    if (scale >= pixelGridThreshold) {
      ctxGrid.save();
      ctxGrid.translate(finalTranslateX * dpr, finalTranslateY * dpr);
      ctxGrid.scale(scale, scale);

      const basePixelRatio = imageLayout.width / image.naturalWidth;

      ctxGrid.beginPath();
      ctxGrid.strokeStyle = "rgba(255, 255, 255, 0.35)";
      // Line width needs to be scaled down to appear constant
      ctxGrid.lineWidth = 0.5 / scale;

      // Draw vertical lines
      for (let i = 1; i < image.naturalWidth; i++) {
        const x = imageLayout.x + i * basePixelRatio;
        ctxGrid.moveTo(x, imageLayout.y);
        ctxGrid.lineTo(x, imageLayout.y + imageLayout.height);
      }

      // Draw horizontal lines
      for (let j = 1; j < image.naturalHeight; j++) {
        const y = imageLayout.y + j * basePixelRatio;
        ctxGrid.moveTo(imageLayout.x, y);
        ctxGrid.lineTo(imageLayout.x + imageLayout.width, y);
      }
      ctxGrid.stroke();
      ctxGrid.restore();
    }
  }

  // --- Layout Calculation & High-DPI Handling ---
  function recalculateLayout() {
    if (!containerEl || !imageCanvasEl || !gridCanvasEl || !image) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = containerEl.getBoundingClientRect();

    // Store old layout for viewport preservation
    const oldLayout = { ...imageLayout };
    const oldRect = {
      width: imageCanvasEl.width / dpr,
      height: imageCanvasEl.height / dpr,
    };

    // Set canvas physical pixels
    imageCanvasEl.width = rect.width * dpr;
    imageCanvasEl.height = rect.height * dpr;
    gridCanvasEl.width = rect.width * dpr;
    gridCanvasEl.height = rect.height * dpr;

    // Set canvas CSS display size
    imageCanvasEl.style.width = `${rect.width}px`;
    imageCanvasEl.style.height = `${rect.height}px`;
    gridCanvasEl.style.width = `${rect.width}px`;
    gridCanvasEl.style.height = `${rect.height}px`;

    if (ctxImage) ctxImage.scale(dpr, dpr);
    if (ctxGrid) ctxGrid.scale(dpr, dpr);

    // Calculate `object-contain` layout in CSS pixels
    const containerAspect = rect.width / rect.height;
    const imageAspect = image.naturalWidth / image.naturalHeight;
    let w, h, x, y;
    if (imageAspect > containerAspect) {
      w = rect.width;
      h = w / imageAspect;
      x = 0;
      y = (rect.height - h) / 2;
    } else {
      h = rect.height;
      w = h * imageAspect;
      y = 0;
      x = (rect.width - w) / 2;
    }

    const newLayout = { x, y, width: w, height: h };

    // Adjust translate to keep viewport top-left corner fixed
    if (oldLayout.width > 0 && oldRect.width > 0) {
      // Calculate what point in the image space is at viewport (0, 0)
      // In image space: viewportPoint = (imageLayout.point * scale + translate) / scale
      // We want: oldViewportPoint = newViewportPoint

      // The point at viewport (0,0) in image coordinate space before resize:
      const oldImagePointX = (0 - translateX) / scale - oldLayout.x;
      const oldImagePointY = (0 - translateY) / scale - oldLayout.y;

      // After resize, we want the same image point to be at viewport (0,0)
      // 0 = (newLayout.x + oldImagePointX) * scale + newTranslateX
      // newTranslateX = -(newLayout.x + oldImagePointX) * scale
      translateX = -(newLayout.x + oldImagePointX) * scale;
      translateY = -(newLayout.y + oldImagePointY) * scale;
    }

    imageLayout = newLayout;
  }

  // --- Lifecycle and Effects ---
  onMount(() => {
    ctxImage = imageCanvasEl!.getContext("2d");
    ctxGrid = gridCanvasEl!.getContext("2d");
  });

  let resizeObserver: ResizeObserver;
  onMount(() => {
    resizeObserver = new ResizeObserver(() => {
      recalculateLayout();
    });
    if (containerEl) resizeObserver.observe(containerEl);
  });
  onDestroy(() => {
    if (resizeObserver) resizeObserver.disconnect();
  });

  // Effect to load the image when `src` changes
  $effect(() => {
    // Check if src is null or undefined
    if (!src) {
      image = null;
      // Reset view state
      scale = initScale;
      translateX = 0;
      translateY = 0;
      // Clear canvases when no image
      if (ctxImage && imageCanvasEl) {
        ctxImage.clearRect(0, 0, imageCanvasEl.width, imageCanvasEl.height);
      }
      if (ctxGrid && gridCanvasEl) {
        ctxGrid.clearRect(0, 0, gridCanvasEl.width, gridCanvasEl.height);
      }
      return;
    }

    const img = new Image();
    img.onload = () => {
      image = img;
      recalculateLayout();
    };
    img.src = src;
  });

  // Effect to re-render the canvas whenever the view changes
  $effect(() => {
    // This effect depends on all variables that affect the view
    if (scale || translateX || translateY || imageLayout) {
      render();
    }
  });

  // --- Event Handlers ---
  function handleKeyDown(event: KeyboardEvent) {
    if (event.code === "Space") {
      event.preventDefault();
      isSpacePressed = true;
    }
    if (event.key === "Alt") {
      event.preventDefault(); // Prevent Alt from activating browser menu
    }
    if (event.key === "Escape") {
      event.preventDefault();
      containerEl?.blur();
    }
  }
  function handleKeyUp(event: KeyboardEvent) {
    if (event.code === "Space") {
      isSpacePressed = false;
      isDragging = false;
    }
  }
  function handleWheel(event: WheelEvent) {
    // Disable zoom when no image
    if (!image) return;

    // Allow zoom with Alt key or without any modifier
    if (event.altKey) {
      event.preventDefault(); // Prevent Alt+Wheel from triggering browser navigation
    }
    // Only process wheel events without modifiers or with Alt key
    if (!event.altKey && (event.ctrlKey || event.metaKey || event.shiftKey)) {
      return;
    }
    containerEl?.focus();

    event.preventDefault();
    const oldScale = scale;
    const isZoomingIn = event.deltaY < 0;
    let newScale = isZoomingIn ? oldScale * zoomFactor : oldScale / zoomFactor;
    newScale = Math.max(minScale, Math.min(maxScale, newScale));
    if (newScale === oldScale) return;

    const rect = (event.currentTarget as HTMLDivElement).getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;

    // Calculate zoom around mouse position
    // Get centering offset at current scale
    const oldCenterOffset = getCenteringOffset();
    const oldFinalTranslateX = translateX + oldCenterOffset.x;
    const oldFinalTranslateY = translateY + oldCenterOffset.y;

    // Calculate new translate to zoom around mouse position
    translateX = mouseX - (mouseX - oldFinalTranslateX) * (newScale / oldScale);
    translateY = mouseY - (mouseY - oldFinalTranslateY) * (newScale / oldScale);

    // Update scale
    scale = newScale;

    // Subtract the new centering offset to get the actual translateX/Y
    // (since centering offset will be added back during rendering)
    const newCenterOffset = getCenteringOffset();
    translateX -= newCenterOffset.x;
    translateY -= newCenterOffset.y;
  }
  function handleMouseDown(event: MouseEvent) {
    // Disable dragging when no image
    if (!image) return;

    containerEl?.focus();
    if (!isSpacePressed || event.button !== 0) return;
    isDragging = true;
    startDrag = { x: event.clientX, y: event.clientY, translateX: translateX, translateY: translateY };
    event.preventDefault();
  }
  function handleMouseMove(event: MouseEvent) {
    if (!isDragging) return;
    const dx = event.clientX - startDrag.x;
    const dy = event.clientY - startDrag.y;
    translateX = startDrag.translateX + dx;
    translateY = startDrag.translateY + dy;
  }
  function handleMouseUp() {
    isDragging = false;
  }
  function resetView() {
    scale = initScale;
    translateX = 0;
    translateY = 0;
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
  bind:this={containerEl}
  class={cn(
    "group relative h-full w-full overflow-hidden select-none focus:outline-none",
    isSpacePressed && !isDragging && "cursor-grab",
    isDragging && "cursor-grabbing",
    className,
  )}
  tabindex="-1"
  role="application"
  aria-label="Interactive image viewer"
  onkeydown={handleKeyDown}
  onkeyup={handleKeyUp}
  onwheel={handleWheel}
  onmousedown={handleMouseDown}
  onmousemove={handleMouseMove}
  onmouseup={handleMouseUp}
  onmouseleave={handleMouseUp}
>
  <!-- Canvas layers for drawing -->
  <canvas bind:this={imageCanvasEl} class="absolute top-0 left-0"></canvas>
  <canvas bind:this={gridCanvasEl} class="absolute top-0 left-0"></canvas>

  <!-- No Image Placeholder -->
  {#if !src}
    <div class="absolute inset-0 flex items-center justify-center">
      <div class="text-muted-foreground text-lg">No Image</div>
    </div>
  {/if}

  <!-- Virtual object to show ring after canvas -->
  <div
    class="group-focus-within:ring-ring pointer-events-none absolute inset-0 rounded-md group-focus-within:ring-1 group-focus-within:ring-inset"
  ></div>

  {#if image}
    <div class="absolute top-2 right-2 z-10">
      <Button variant="outline" size="icon" onclick={resetView} title="Reset View">
        <RefreshCcw class="h-4 w-4" />
      </Button>
    </div>
  {/if}
</div>

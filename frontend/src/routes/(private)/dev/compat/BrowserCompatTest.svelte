<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";

  type $$props = {
    class?: string;
  };

  let { class: className }: $$props = $props();

  // Test states
  let transformSupported = $state(false);
  let oklabSupported = $state(false);
  let colorMixSupported = $state(false);
  let nestingSupported = $state(false);
  let mediaRangeSupported = $state(false);

  // Feature detection
  $effect(() => {
    // Test for translate/scale/rotate support
    transformSupported = CSS.supports("translate", "10px");

    // Test for oklab support
    oklabSupported = CSS.supports("color", "oklab(0.5 0.2 0.1)");

    // Test for color-mix support
    colorMixSupported = CSS.supports("color", "color-mix(in srgb, red, blue)");

    // Test for CSS nesting
    nestingSupported = CSS.supports("selector(&)");

    // Test for media query range syntax
    mediaRangeSupported = window.matchMedia("(width >= 0px)").media !== "not all";
  });
</script>

<ScrollArea class="h-full w-full">
  <div class={cn("w-full space-y-8 p-6", className)}>
    <!-- Page Header -->
    <div class="space-y-2">
      <h1 class="text-3xl font-bold">Browser Compatibility Test</h1>
      <p class="text-muted-foreground">Testing PostCSS polyfills for Tailwind v4 backwards compatibility</p>
      <div class="text-muted-foreground text-sm">Target: Chrome 99+, Safari 15.5+, iOS Safari 16.1+</div>
    </div>

    <!-- Feature Support Status -->
    <div class="bg-card space-y-3 rounded-lg border p-6">
      <h2 class="mb-4 text-xl font-semibold">Native Browser Support Detection</h2>
      <div class="grid gap-2">
        <div class="bg-muted/50 flex items-center justify-between rounded p-2">
          <span>Transform Shortcuts (translate/scale/rotate)</span>
          <span class={transformSupported ? "text-green-600" : "text-orange-600"}>
            {transformSupported ? "✓ Native" : "⚠ Polyfilled"}
          </span>
        </div>
        <div class="bg-muted/50 flex items-center justify-between rounded p-2">
          <span>OKLAB Color Space</span>
          <span class={oklabSupported ? "text-green-600" : "text-orange-600"}>
            {oklabSupported ? "✓ Native" : "⚠ Polyfilled"}
          </span>
        </div>
        <div class="bg-muted/50 flex items-center justify-between rounded p-2">
          <span>color-mix() Function</span>
          <span class={colorMixSupported ? "text-green-600" : "text-orange-600"}>
            {colorMixSupported ? "✓ Native" : "⚠ Polyfilled"}
          </span>
        </div>
        <div class="bg-muted/50 flex items-center justify-between rounded p-2">
          <span>CSS Nesting</span>
          <span class={nestingSupported ? "text-green-600" : "text-orange-600"}>
            {nestingSupported ? "✓ Native" : "⚠ Polyfilled"}
          </span>
        </div>
        <div class="bg-muted/50 flex items-center justify-between rounded p-2">
          <span>Media Query Range Syntax</span>
          <span class={mediaRangeSupported ? "text-green-600" : "text-orange-600"}>
            {mediaRangeSupported ? "✓ Native" : "⚠ Polyfilled"}
          </span>
        </div>
      </div>
    </div>

    <!-- Test 1: Transform Shortcuts -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">1. Transform Shortcuts</h2>
      <p class="text-muted-foreground text-sm">
        Testing translate, scale, and rotate properties (polyfilled to transform for older browsers)
      </p>

      <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div class="space-y-2">
          <div class="text-sm font-medium">Translate</div>
          <div class="bg-muted flex h-24 items-center justify-center rounded">
            <div
              class="h-16 w-16 translate-x-4 translate-y-2 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 shadow-lg"
            ></div>
          </div>
          <code class="text-xs">translate-x-4 translate-y-2</code>
        </div>

        <div class="space-y-2">
          <div class="text-sm font-medium">Scale</div>
          <div class="bg-muted flex h-24 items-center justify-center rounded">
            <div class="h-16 w-16 scale-125 rounded-lg bg-gradient-to-br from-green-500 to-teal-600 shadow-lg"></div>
          </div>
          <code class="text-xs">scale-125</code>
        </div>

        <div class="space-y-2">
          <div class="text-sm font-medium">Rotate</div>
          <div class="bg-muted flex h-24 items-center justify-center rounded">
            <div class="h-16 w-16 rotate-12 rounded-lg bg-gradient-to-br from-orange-500 to-red-600 shadow-lg"></div>
          </div>
          <code class="text-xs">rotate-12</code>
        </div>
      </div>

      <div class="space-y-2">
        <div class="text-sm font-medium">Combined Transform</div>
        <div class="bg-muted flex h-24 items-center justify-center rounded">
          <div
            class="h-16 w-16 translate-x-8 rotate-6 scale-110 rounded-lg bg-gradient-to-br from-pink-500 to-rose-600 shadow-lg"
          ></div>
        </div>
        <code class="text-xs">translate-x-8 scale-110 rotate-6</code>
        <div class="text-muted-foreground text-xs">⚠️ Note: Independent transforms need @apply for older browsers</div>
      </div>
    </div>

    <!-- Test 2: Modern Color Functions (OKLAB) -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">2. OKLAB Color Space</h2>
      <p class="text-muted-foreground text-sm">Testing oklab() color function (pre-computed for older browsers)</p>

      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: oklab(0.65 0.15 0.1);"></div>
          <code class="block text-xs">oklab(0.65 0.15 0.1)</code>
        </div>
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: oklab(0.5 -0.1 0.15);"></div>
          <code class="block text-xs">oklab(0.5 -0.1 0.15)</code>
        </div>
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: oklab(0.7 0.2 -0.15);"></div>
          <code class="block text-xs">oklab(0.7 0.2 -0.15)</code>
        </div>
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: oklab(0.45 -0.15 -0.1);"></div>
          <code class="block text-xs">oklab(0.45 -0.15 -0.1)</code>
        </div>
      </div>
    </div>

    <!-- Test 3: Color Mix -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">3. color-mix() Function</h2>
      <p class="text-muted-foreground text-sm">
        Testing color-mix() with CSS variables (pre-computed for older browsers)
      </p>

      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: color-mix(in srgb, red, blue);"></div>
          <code class="block text-xs">color-mix(in srgb, red, blue)</code>
        </div>
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: color-mix(in srgb, green 70%, yellow);"></div>
          <code class="block text-xs">color-mix(in srgb, green 70%, yellow)</code>
        </div>
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: color-mix(in srgb, purple, white 50%);"></div>
          <code class="block text-xs">color-mix(in srgb, purple, white 50%)</code>
        </div>
        <div class="space-y-2">
          <div class="h-20 rounded-lg shadow-md" style="background: color-mix(in srgb, orange, transparent 30%);"></div>
          <code class="block text-xs">color-mix(in srgb, orange, transparent 30%)</code>
        </div>
      </div>
    </div>

    <!-- Test 4: Gradient with Colorspace -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">4. Gradients with Color Space</h2>
      <p class="text-muted-foreground text-sm">Testing gradients (colorspace removed for older browsers)</p>

      <div class="space-y-4">
        <div class="space-y-2">
          <div class="text-sm font-medium">Linear Gradient</div>
          <div class="h-20 rounded-lg bg-gradient-to-r from-blue-500 via-purple-500 to-pink-500 shadow-md"></div>
          <code class="text-xs">bg-gradient-to-r from-blue-500 via-purple-500 to-pink-500</code>
        </div>

        <div class="space-y-2">
          <div class="text-sm font-medium">With Stops</div>
          <div
            class="h-20 rounded-lg shadow-md"
            style="background: linear-gradient(to right, red 0%, yellow 50%, green 100%);"
          ></div>
          <code class="text-xs">linear-gradient(to right, red 0%, yellow 50%, green 100%)</code>
          <div class="text-muted-foreground text-xs">
            ⚠️ Always use 'via' stop in gradients for Safari compatibility
          </div>
        </div>
      </div>
    </div>

    <!-- Test 5: CSS Nesting & Dark Mode -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">5. CSS Nesting (Dark Mode)</h2>
      <p class="text-muted-foreground text-sm">
        Testing nested CSS used by dark: variant (polyfilled for older browsers)
      </p>

      <div class="grid grid-cols-2 gap-4">
        <div class="space-y-2">
          <div class="text-sm font-medium">Light Mode</div>
          <div
            class="rounded-lg border border-gray-300 bg-white p-6 text-gray-900 shadow-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
          >
            <p class="font-medium">Themed Content</p>
            <p class="mt-2 text-sm text-gray-600 dark:text-gray-300">This switches between light and dark mode</p>
          </div>
        </div>

        <div class="space-y-2">
          <div class="text-sm font-medium">Hover State</div>
          <div
            class="cursor-pointer rounded-lg border bg-blue-100 p-6 shadow-sm transition-colors hover:bg-blue-200 dark:bg-blue-900 dark:hover:bg-blue-800"
          >
            <p class="font-medium text-blue-900 dark:text-blue-100">Hover Me</p>
            <p class="mt-2 text-sm text-blue-700 dark:text-blue-200">Hover to see nested styles</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Test 6: Media Query Range Syntax -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">6. Media Query Range Syntax</h2>
      <p class="text-muted-foreground text-sm">
        Testing modern media query syntax (converted to min-width/max-width for older browsers)
      </p>

      <div class="space-y-2">
        <div class="rounded-lg border bg-red-100 p-4 text-gray-900 md:bg-yellow-100 lg:bg-green-100">
          <p class="font-medium">Responsive Box</p>
          <p class="mt-2 text-sm">
            <span class="md:hidden">📱 Mobile (Red)</span>
            <span class="hidden md:inline lg:hidden">💻 Tablet (Yellow)</span>
            <span class="hidden lg:inline">🖥️ Desktop (Green)</span>
          </p>
        </div>
        <div class="text-muted-foreground space-y-1 text-xs">
          <div>Uses Tailwind breakpoints: sm (640px), md (768px), lg (1024px)</div>
          <div>Polyfill converts: (width >= 768px) → (min-width: 768px)</div>
        </div>
      </div>
    </div>

    <!-- Test 7: CSS Variables with Empty Fallbacks -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">7. CSS Variable Fallbacks</h2>
      <p class="text-muted-foreground text-sm">
        Testing CSS variables with empty fallbacks (space added for older browsers)
      </p>

      <div class="space-y-2">
        <div class="rounded-lg border p-4" style="background: var(--test-undefined-color, #8b5cf6);">
          <p class="font-medium text-white">Fallback Color Test</p>
          <code class="text-xs text-white/80">var(--test-undefined-color, #8b5cf6)</code>
        </div>
        <div class="text-muted-foreground text-xs">Polyfill ensures var(--foo,) becomes var(--foo, ) with a space</div>
      </div>
    </div>

    <!-- Test 8: Tailwind Opacity Modifiers (Color-Mix Polyfill) -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">8. Tailwind Opacity Modifiers (color-mix polyfill)</h2>
      <p class="text-muted-foreground text-sm">
        Testing Tailwind's `/opacity` syntax which generates color-mix() under the hood
      </p>

      <!-- Theme Colors -->
      <div class="space-y-3">
        <h3 class="text-base font-semibold">Theme Colors (from app.css)</h3>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div class="space-y-2">
            <div class="text-xs font-medium">100% Opacity</div>
            <div class="bg-primary h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-primary</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">75% Opacity</div>
            <div class="bg-primary/75 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-primary/75</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">50% Opacity</div>
            <div class="bg-primary/50 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-primary/50</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">20% Opacity</div>
            <div class="bg-primary/20 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-primary/20</code>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div class="space-y-2">
            <div class="text-xs font-medium">Destructive</div>
            <div class="bg-destructive h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-destructive</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Destructive 75%</div>
            <div class="bg-destructive/75 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-destructive/75</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Foreground</div>
            <div class="bg-foreground h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-foreground</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Foreground 20%</div>
            <div class="bg-foreground/20 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">bg-foreground/20</code>
          </div>
        </div>
      </div>

      <!-- Built-in Tailwind Colors -->
      <div class="space-y-3">
        <h3 class="text-base font-semibold">Tailwind Built-in Colors</h3>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div class="space-y-2">
            <div class="text-xs font-medium">Orange 500</div>
            <div class="h-20 rounded-lg bg-orange-500 shadow-md"></div>
            <code class="block text-xs">bg-orange-500</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Orange 500/75</div>
            <div class="h-20 rounded-lg bg-orange-500/75 shadow-md"></div>
            <code class="block text-xs">bg-orange-500/75</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Orange 500/50</div>
            <div class="h-20 rounded-lg bg-orange-500/50 shadow-md"></div>
            <code class="block text-xs">bg-orange-500/50</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Orange 500/20</div>
            <div class="h-20 rounded-lg bg-orange-500/20 shadow-md"></div>
            <code class="block text-xs">bg-orange-500/20</code>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div class="space-y-2">
            <div class="text-xs font-medium">Blue 600</div>
            <div class="h-20 rounded-lg bg-blue-600 shadow-md"></div>
            <code class="block text-xs">bg-blue-600</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Blue 600/50</div>
            <div class="h-20 rounded-lg bg-blue-600/50 shadow-md"></div>
            <code class="block text-xs">bg-blue-600/50</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Green 500</div>
            <div class="h-20 rounded-lg bg-green-500 shadow-md"></div>
            <code class="block text-xs">bg-green-500</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Green 500/50</div>
            <div class="h-20 rounded-lg bg-green-500/50 shadow-md"></div>
            <code class="block text-xs">bg-green-500/50</code>
          </div>
        </div>
      </div>

      <!-- Border Example -->
      <div class="space-y-3">
        <h3 class="text-base font-semibold">Border Colors with Opacity</h3>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
          <div class="space-y-2">
            <div class="text-xs font-medium">Border Border</div>
            <div class="bg-muted border-border h-20 rounded-lg border-4 shadow-md"></div>
            <code class="block text-xs">border-border</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Border Border/50</div>
            <div class="bg-muted border-border/50 h-20 rounded-lg border-4 shadow-md"></div>
            <code class="block text-xs">border-border/50</code>
          </div>
          <div class="space-y-2">
            <div class="text-xs font-medium">Border Primary/50</div>
            <div class="bg-muted h-20 rounded-lg border-4 border-primary/50 shadow-md"></div>
            <code class="block text-xs">border-primary/50</code>
          </div>
        </div>
      </div>

      <!-- Text Example -->
      <div class="space-y-3">
        <h3 class="text-base font-semibold">Text Colors with Opacity</h3>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
          <div class="space-y-2">
            <div class="bg-muted text-primary rounded-lg p-4 font-bold">Text Primary</div>
            <code class="block text-xs">text-primary</code>
          </div>
          <div class="space-y-2">
            <div class="bg-muted text-primary/50 rounded-lg p-4 font-bold">Text Primary 50%</div>
            <code class="block text-xs">text-primary/50</code>
          </div>
          <div class="space-y-2">
            <div class="bg-muted rounded-lg p-4 font-bold text-orange-500/50">Text Orange 50%</div>
            <code class="block text-xs">text-orange-500/50</code>
          </div>
        </div>
      </div>

      <div class="text-muted-foreground space-y-1 text-xs">
        <div>✅ All opacity modifiers use color-mix() internally</div>
        <div>✅ PostCSS converts to rgb(var(--color-xxx-rgb) / opacity) for Chrome 87+ compatibility</div>
        <div>✅ Dark mode switching works correctly with opacity modifiers</div>
      </div>
    </div>

    <!-- Test 9: Complex Combined Test -->
    <div class="bg-card space-y-4 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">9. Combined Features Test</h2>
      <p class="text-muted-foreground text-sm">Testing multiple polyfilled features together</p>

      <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div
          class="h-32 cursor-pointer rounded-lg shadow-lg transition-all hover:rotate-3 hover:scale-110"
          style="background: linear-gradient(135deg, color-mix(in srgb, blue, purple) 0%, color-mix(in srgb, purple, pink) 100%);"
        ></div>
        <div
          class="h-32 cursor-pointer rounded-lg shadow-lg transition-all hover:-rotate-3 hover:scale-110"
          style="background: linear-gradient(135deg, oklab(0.7 0.15 0.1) 0%, oklab(0.5 0.2 -0.1) 100%);"
        ></div>
        <div
          class="h-32 cursor-pointer rounded-lg bg-gradient-to-br from-green-400 to-blue-500 shadow-lg transition-all hover:rotate-6 hover:scale-110"
        ></div>
        <div
          class="h-32 cursor-pointer rounded-lg bg-gradient-to-br from-orange-400 to-pink-500 shadow-lg transition-all hover:-rotate-6 hover:scale-110 dark:from-orange-600 dark:to-pink-700"
        ></div>
      </div>
      <div class="text-muted-foreground text-xs">
        Hover over cards to see transform + color mixing + gradients working together
      </div>
    </div>

    <!-- Browser Info -->
    <div class="bg-card space-y-3 rounded-lg border p-6">
      <h2 class="text-xl font-semibold">Browser Information</h2>
      <div class="grid gap-2 font-mono text-sm">
        <div class="bg-muted/50 flex justify-between rounded p-2">
          <span>User Agent:</span>
          <span class="ml-4 max-w-md truncate text-right text-xs" title={navigator.userAgent}>
            {navigator.userAgent}
          </span>
        </div>
        <div class="bg-muted/50 flex justify-between rounded p-2">
          <span>Viewport:</span>
          <span>{window.innerWidth} × {window.innerHeight}</span>
        </div>
      </div>
    </div>

    <!-- Instructions -->
    <div class="space-y-3 rounded-lg border border-blue-500/50 bg-blue-50 p-6 dark:bg-blue-950/30">
      <h2 class="text-xl font-semibold text-blue-900 dark:text-blue-100">Testing Instructions</h2>
      <ul class="space-y-2 text-sm text-blue-800 dark:text-blue-200">
        <li>• Check the "Native Browser Support Detection" section to see what your browser supports natively</li>
        <li>• Orange "⚠ Polyfilled" status means PostCSS plugins are handling the feature</li>
        <li>• Green "✓ Native" status means your browser supports the feature without polyfills</li>
        <li>• All tests should render correctly regardless of native support status</li>
        <li>• Try testing in: iOS Safari 15.5+, iOS Safari 16.1+, Chrome 90+, Chrome 99+</li>
        <li>• Hover over interactive elements to test transform polyfills</li>
        <li>• Toggle dark mode to test CSS nesting polyfills</li>
        <li>• Resize the window to test media query polyfills</li>
      </ul>
    </div>
  </div>
</ScrollArea>

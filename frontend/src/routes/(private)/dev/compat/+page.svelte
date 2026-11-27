<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";

  type $props = {
    class?: string;
  };

  let { class: className }: $props = $props();

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

<div class="h-full w-full overflow-auto p-4">
  <div class={cn("w-full space-y-4", className)}>
    <!--
      No page title here: the layout header already shows the entry's label
      ("Browser Compatibility"), so a heading would repeat it. The intro keeps
      only what the header does not say.
    -->
    <div class="space-y-2">
      <p class="text-muted-foreground">Testing PostCSS polyfills for Tailwind v4 backwards compatibility</p>
      <div class="text-sm text-muted-foreground">Target: Chrome 99+, Safari 15.5+, iOS Safari 16.1+</div>
    </div>

    <!-- Feature Support Status -->
    <Card.Root>
      <Card.Header>
        <Card.Title>Native Browser Support Detection</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid gap-2">
          <div class="flex items-center justify-between rounded bg-muted/50 p-2">
            <span>Transform Shortcuts (translate/scale/rotate)</span>
            <span class={transformSupported ? "text-green-600" : "text-orange-600"}>
              {transformSupported ? "✓ Native" : "⚠ Polyfilled"}
            </span>
          </div>
          <div class="flex items-center justify-between rounded bg-muted/50 p-2">
            <span>OKLAB Color Space</span>
            <span class={oklabSupported ? "text-green-600" : "text-orange-600"}>
              {oklabSupported ? "✓ Native" : "⚠ Polyfilled"}
            </span>
          </div>
          <div class="flex items-center justify-between rounded bg-muted/50 p-2">
            <span>color-mix() Function</span>
            <span class={colorMixSupported ? "text-green-600" : "text-orange-600"}>
              {colorMixSupported ? "✓ Native" : "⚠ Polyfilled"}
            </span>
          </div>
          <div class="flex items-center justify-between rounded bg-muted/50 p-2">
            <span>CSS Nesting</span>
            <span class={nestingSupported ? "text-green-600" : "text-orange-600"}>
              {nestingSupported ? "✓ Native" : "⚠ Polyfilled"}
            </span>
          </div>
          <div class="flex items-center justify-between rounded bg-muted/50 p-2">
            <span>Media Query Range Syntax</span>
            <span class={mediaRangeSupported ? "text-green-600" : "text-orange-600"}>
              {mediaRangeSupported ? "✓ Native" : "⚠ Polyfilled"}
            </span>
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 1: Transform Shortcuts -->
    <Card.Root>
      <Card.Header>
        <Card.Title>1. Transform Shortcuts</Card.Title>
        <Card.Description>
          Testing translate, scale, and rotate properties (polyfilled to transform for older browsers)
        </Card.Description>
      </Card.Header>
      <Card.Content class="space-y-4">
        <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div class="space-y-2">
            <div class="text-sm font-medium">Translate</div>
            <div class="flex h-24 items-center justify-center rounded bg-muted">
              <div
                class="h-16 w-16 translate-x-4 translate-y-2 rounded-lg bg-linear-to-br from-blue-500 to-purple-600 shadow-lg"
              ></div>
            </div>
            <code class="text-xs">translate-x-4 translate-y-2</code>
          </div>

          <div class="space-y-2">
            <div class="text-sm font-medium">Scale</div>
            <div class="flex h-24 items-center justify-center rounded bg-muted">
              <div class="h-16 w-16 scale-125 rounded-lg bg-linear-to-br from-green-500 to-teal-600 shadow-lg"></div>
            </div>
            <code class="text-xs">scale-125</code>
          </div>

          <div class="space-y-2">
            <div class="text-sm font-medium">Rotate</div>
            <div class="flex h-24 items-center justify-center rounded bg-muted">
              <div class="h-16 w-16 rotate-12 rounded-lg bg-linear-to-br from-orange-500 to-red-600 shadow-lg"></div>
            </div>
            <code class="text-xs">rotate-12</code>
          </div>
        </div>

        <div class="space-y-2">
          <div class="text-sm font-medium">Combined Transform</div>
          <div class="flex h-24 items-center justify-center rounded bg-muted">
            <div
              class="h-16 w-16 translate-x-8 scale-110 rotate-6 rounded-lg bg-linear-to-br from-pink-500 to-rose-600 shadow-lg"
            ></div>
          </div>
          <code class="text-xs">translate-x-8 scale-110 rotate-6</code>
          <div class="text-xs text-muted-foreground">
            ⚠️ Note: Independent transforms need @apply for older browsers
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 2: Modern Color Functions (OKLAB) -->
    <Card.Root>
      <Card.Header>
        <Card.Title>2. OKLAB Color Space</Card.Title>
        <Card.Description>Testing oklab() color function (compiled to rgb() for older browsers)</Card.Description>
      </Card.Header>
      <Card.Content>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div class="space-y-2">
            <div class="swatch-oklab-1 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">oklab(0.65 0.15 0.1)</code>
          </div>
          <div class="space-y-2">
            <div class="swatch-oklab-2 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">oklab(0.5 -0.1 0.15)</code>
          </div>
          <div class="space-y-2">
            <div class="swatch-oklab-3 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">oklab(0.7 0.2 -0.15)</code>
          </div>
          <div class="space-y-2">
            <div class="swatch-oklab-4 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">oklab(0.45 -0.15 -0.1)</code>
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 3: Color Mix -->
    <Card.Root>
      <Card.Header>
        <Card.Title>3. color-mix() Function</Card.Title>
        <Card.Description>
          Testing color-mix() with static colors (compiled to rgb() for older browsers)
        </Card.Description>
      </Card.Header>
      <Card.Content>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div class="space-y-2">
            <div class="swatch-mix-1 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">color-mix(in srgb, red, blue)</code>
          </div>
          <div class="space-y-2">
            <div class="swatch-mix-2 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">color-mix(in srgb, green 70%, yellow)</code>
          </div>
          <div class="space-y-2">
            <div class="swatch-mix-3 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">color-mix(in srgb, purple, white 50%)</code>
          </div>
          <div class="space-y-2">
            <div class="swatch-mix-4 h-20 rounded-lg shadow-md"></div>
            <code class="block text-xs">color-mix(in srgb, orange, transparent 30%)</code>
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 4: Gradient with Colorspace -->
    <Card.Root>
      <Card.Header>
        <Card.Title>4. Gradients with Color Space</Card.Title>
        <Card.Description>Testing gradients (colorspace removed for older browsers)</Card.Description>
      </Card.Header>
      <Card.Content class="space-y-4">
        <div class="space-y-2">
          <div class="text-sm font-medium">Linear Gradient</div>
          <div class="h-20 rounded-lg bg-linear-to-r from-blue-500 via-purple-500 to-pink-500 shadow-md"></div>
          <code class="text-xs">bg-linear-to-r from-blue-500 via-purple-500 to-pink-500</code>
        </div>

        <div class="space-y-2">
          <div class="text-sm font-medium">With Stops</div>
          <div
            class="h-20 rounded-lg shadow-md"
            style="background: linear-gradient(to right, red 0%, yellow 50%, green 100%);"
          ></div>
          <code class="text-xs">linear-gradient(to right, red 0%, yellow 50%, green 100%)</code>
          <div class="text-xs text-muted-foreground">
            ⚠️ Always use 'via' stop in gradients for Safari compatibility
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 5: CSS Nesting & Dark Mode -->
    <Card.Root>
      <Card.Header>
        <Card.Title>5. CSS Nesting (Dark Mode)</Card.Title>
        <Card.Description>Testing nested CSS used by dark: variant (polyfilled for older browsers)</Card.Description>
      </Card.Header>
      <Card.Content>
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

        <div class="mt-4 grid grid-cols-2 gap-4">
          <div class="space-y-2">
            <div class="text-sm font-medium">Nested Rule</div>
            <div class="nest-probe rounded-lg p-4">
              <div class="nest-probe-child h-10 rounded"></div>
            </div>
          </div>

          <div class="space-y-2">
            <div class="text-sm font-medium">Flat Rule (Reference)</div>
            <div class="rounded-lg bg-violet-500 p-4">
              <div class="nest-oracle-child h-10 rounded"></div>
            </div>
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 6: Media Query Range Syntax -->
    <Card.Root>
      <Card.Header>
        <Card.Title>6. Media Query Range Syntax</Card.Title>
        <Card.Description>
          Testing modern media query syntax (converted to min-width/max-width for older browsers)
        </Card.Description>
      </Card.Header>
      <Card.Content>
        <div class="space-y-2">
          <div class="rounded-lg border bg-red-100 p-4 text-gray-900 md:bg-yellow-100 lg:bg-green-100">
            <p class="font-medium">Responsive Box</p>
            <p class="mt-2 text-sm">
              <span class="md:hidden">📱 Mobile (Red)</span>
              <span class="hidden md:inline lg:hidden">💻 Tablet (Yellow)</span>
              <span class="hidden lg:inline">🖥️ Desktop (Green)</span>
            </p>
          </div>
          <div class="space-y-1 text-xs text-muted-foreground">
            <div>Uses Tailwind breakpoints: sm (640px), md (768px), lg (1024px)</div>
            <div>Polyfill converts: (width >= 768px) → (min-width: 768px)</div>
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 7: CSS Variables with Empty Fallbacks -->
    <Card.Root>
      <Card.Header>
        <Card.Title>7. CSS Variable Fallbacks</Card.Title>
        <Card.Description>Testing CSS variables with empty fallbacks (space added for older browsers)</Card.Description>
      </Card.Header>
      <Card.Content>
        <div class="space-y-2">
          <div class="var-fallback-probe rounded-lg border p-4">
            <p class="font-medium text-white">Fallback Color Test</p>
            <code class="text-xs text-white/80">var(--test-undefined-color, #8b5cf6)</code>
          </div>
          <div class="text-xs text-muted-foreground">
            Polyfill ensures var(--foo,) becomes var(--foo, ) with a space
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 8: Tailwind Opacity Modifiers (Color-Mix Polyfill) -->
    <Card.Root>
      <Card.Header>
        <Card.Title>8. Tailwind Opacity Modifiers (color-mix polyfill)</Card.Title>
        <Card.Description>
          Testing Tailwind's `/opacity` syntax which generates color-mix() under the hood
        </Card.Description>
      </Card.Header>
      <Card.Content class="space-y-4">
        <!-- Theme Colors -->
        <div class="space-y-3">
          <h3 class="text-base font-semibold">Theme Colors (from app.css)</h3>
          <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div class="space-y-2">
              <div class="text-xs font-medium">100% Opacity</div>
              <div class="h-20 rounded-lg bg-primary shadow-md"></div>
              <code class="block text-xs">bg-primary</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">75% Opacity</div>
              <div class="h-20 rounded-lg bg-primary/75 shadow-md"></div>
              <code class="block text-xs">bg-primary/75</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">50% Opacity</div>
              <div class="h-20 rounded-lg bg-primary/50 shadow-md"></div>
              <code class="block text-xs">bg-primary/50</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">20% Opacity</div>
              <div class="h-20 rounded-lg bg-primary/20 shadow-md"></div>
              <code class="block text-xs">bg-primary/20</code>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div class="space-y-2">
              <div class="text-xs font-medium">Destructive</div>
              <div class="h-20 rounded-lg bg-destructive shadow-md"></div>
              <code class="block text-xs">bg-destructive</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">Destructive 75%</div>
              <div class="h-20 rounded-lg bg-destructive/75 shadow-md"></div>
              <code class="block text-xs">bg-destructive/75</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">Foreground</div>
              <div class="h-20 rounded-lg bg-foreground shadow-md"></div>
              <code class="block text-xs">bg-foreground</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">Foreground 20%</div>
              <div class="h-20 rounded-lg bg-foreground/20 shadow-md"></div>
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
              <div class="h-20 rounded-lg border-4 border-border bg-muted shadow-md"></div>
              <code class="block text-xs">border-border</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">Border Border/50</div>
              <div class="h-20 rounded-lg border-4 border-border/50 bg-muted shadow-md"></div>
              <code class="block text-xs">border-border/50</code>
            </div>
            <div class="space-y-2">
              <div class="text-xs font-medium">Border Primary/50</div>
              <div class="h-20 rounded-lg border-4 border-primary/50 bg-muted shadow-md"></div>
              <code class="block text-xs">border-primary/50</code>
            </div>
          </div>
        </div>

        <!-- Text Example -->
        <div class="space-y-3">
          <h3 class="text-base font-semibold">Text Colors with Opacity</h3>
          <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
            <div class="space-y-2">
              <div class="rounded-lg bg-muted p-4 font-bold text-primary">Text Primary</div>
              <code class="block text-xs">text-primary</code>
            </div>
            <div class="space-y-2">
              <div class="rounded-lg bg-muted p-4 font-bold text-primary/50">Text Primary 50%</div>
              <code class="block text-xs">text-primary/50</code>
            </div>
            <div class="space-y-2">
              <div class="rounded-lg bg-muted p-4 font-bold text-orange-500/50">Text Orange 50%</div>
              <code class="block text-xs">text-orange-500/50</code>
            </div>
          </div>
        </div>

        <div class="space-y-1 text-xs text-muted-foreground">
          <div>✅ All opacity modifiers use color-mix() internally</div>
          <div>✅ PostCSS converts to rgb(var(--color-xxx-rgb) / opacity) for Chrome 87+ compatibility</div>
          <div>✅ Dark mode switching works correctly with opacity modifiers</div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Test 9: Complex Combined Test -->
    <Card.Root>
      <Card.Header>
        <Card.Title>9. Combined Features Test</Card.Title>
        <Card.Description>Testing multiple polyfilled features together</Card.Description>
      </Card.Header>
      <Card.Content>
        <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div
            class="swatch-gradient-mix h-32 cursor-pointer rounded-lg shadow-lg transition-all hover:scale-110 hover:rotate-3"
          ></div>
          <div
            class="swatch-gradient-oklab h-32 cursor-pointer rounded-lg shadow-lg transition-all hover:scale-110 hover:-rotate-3"
          ></div>
          <div
            class="h-32 cursor-pointer rounded-lg bg-linear-to-br from-green-400 to-blue-500 shadow-lg transition-all hover:scale-110 hover:rotate-6"
          ></div>
          <div
            class="h-32 cursor-pointer rounded-lg bg-linear-to-br from-orange-400 to-pink-500 shadow-lg transition-all hover:scale-110 hover:-rotate-6 dark:from-orange-600 dark:to-pink-700"
          ></div>
        </div>
        <div class="mt-4 text-xs text-muted-foreground">
          Hover over cards to see transform + color mixing + gradients working together
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Browser Info -->
    <Card.Root>
      <Card.Header>
        <Card.Title>Browser Information</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid gap-2 font-mono text-sm">
          <div class="flex justify-between rounded bg-muted/50 p-2">
            <span>User Agent:</span>
            <span class="ml-4 max-w-md truncate text-right text-xs" title={navigator.userAgent}>
              {navigator.userAgent}
            </span>
          </div>
          <div class="flex justify-between rounded bg-muted/50 p-2">
            <span>Viewport:</span>
            <span>{window.innerWidth} × {window.innerHeight}</span>
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Instructions -->
    <Card.Root class="border-blue-500/50 bg-blue-50 dark:bg-blue-950/30">
      <Card.Header>
        <Card.Title class="text-blue-900 dark:text-blue-100">Testing Instructions</Card.Title>
      </Card.Header>
      <Card.Content>
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
      </Card.Content>
    </Card.Root>
  </div>
</div>

<style>
  /*
    These swatches must be declared in a stylesheet, not in an inline `style`
    attribute: PostCSS only rewrites the CSS it compiles, so an inline
    `oklab()` or `color-mix()` reaches Chromium 108 unchanged. The engine then
    drops the declaration at computed-value time and the swatch renders
    transparent, which is exactly the bug this page exists to catch.
  */
  .swatch-oklab-1 {
    background: oklab(0.65 0.15 0.1);
  }

  .swatch-oklab-2 {
    background: oklab(0.5 -0.1 0.15);
  }

  .swatch-oklab-3 {
    background: oklab(0.7 0.2 -0.15);
  }

  .swatch-oklab-4 {
    background: oklab(0.45 -0.15 -0.1);
  }

  .swatch-mix-1 {
    background: color-mix(in srgb, red, blue);
  }

  .swatch-mix-2 {
    background: color-mix(in srgb, green 70%, yellow);
  }

  .swatch-mix-3 {
    background: color-mix(in srgb, purple, white 50%);
  }

  .swatch-mix-4 {
    background: color-mix(in srgb, orange, transparent 30%);
  }

  .swatch-gradient-mix {
    background: linear-gradient(135deg, color-mix(in srgb, blue, purple) 0%, color-mix(in srgb, purple, pink) 100%);
  }

  .swatch-gradient-oklab {
    background: linear-gradient(135deg, oklab(0.7 0.15 0.1) 0%, oklab(0.5 0.2 -0.1) 100%);
  }

  /*
    Nesting: the child of `.nest-probe` is painted by a rule written with `&`,
    the child of `.nest-oracle-child` by a flat rule of the same color. Chromium
    108 does not understand nested CSS, so without the polyfill the first child
    paints nothing while the second still paints; the real-browser check
    compares the two.
  */
  .nest-probe {
    background: #8b5cf6;
    & .nest-probe-child {
      background: #f59e0b;
    }
  }

  .nest-oracle-child {
    background: #f59e0b;
  }

  /*
    An undefined variable must fall back to the declared color. The declaration
    lives in a stylesheet and not in a `style` attribute: PostCSS only rewrites
    the CSS it compiles, and this page exists to test what it produces.
  */
  .var-fallback-probe {
    background: var(--test-undefined-color, #8b5cf6);
  }
</style>

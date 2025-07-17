<script lang="ts">
    import { ScrollArea } from "$lib/components/ui/scroll-area";
    import { cn } from "$lib/utils.js";

    // props
    type $$props = {
      activeId?: number;
      class?: string;
      onNavigate?: () => void;
    };
    let { activeId, class: className, onNavigate = () => {} }: $$props = $props();
  
    // Handle item click
    async function handleConfigClick() {
      onNavigate();
    }
  </script>
  
  <aside
    class={cn("bg-background h-full max-h-screen flex flex-col", className)}
    role="navigation"
    aria-label="Main navigation"
  >
    <ScrollArea class="flex-1 min-h-0">
      <div class="space-y-2 p-4" role="list">
        Main navigation
      </div>
    </ScrollArea>
  </aside>
  
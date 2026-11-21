<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();

  const errorMessage = $derived.by(() => {
    const key = sharedState.errorKey;
    if (key === "ConfigNotFound") return t.Error.ConfigNotFound();
    if (key === "PythonNotConfigured") return t.Error.PythonNotConfigured();
    if (key === "PythonNotFound") return t.Error.PythonNotFound();
    if (key === "GuiPyNotFound") return t.Error.GuiPyNotFound();
    return t.Error.UnknownError();
  });
</script>

<div class="dotbg flex h-full items-center justify-center bg-background text-foreground">
  <Card.Root class="neushadow w-120 border-none">
    <Card.Header>
      <Card.Title class="text-4xl font-bold text-destructive">Error</Card.Title>
    </Card.Header>

    <Card.Content>
      <p class="text-lg">
        {errorMessage}
      </p>
      {#if sharedState.errorPath}
        <div class="text-sm">
          <p>{t.Error.CurrentPath()}: {sharedState.errorPath}</p>
        </div>
      {/if}
    </Card.Content>

    <Card.Footer>
      <Button onclick={() => location.reload()} class="h-10 w-full text-lg font-semibold">
        {t.Error.Retry()}
      </Button>
    </Card.Footer>
  </Card.Root>
</div>

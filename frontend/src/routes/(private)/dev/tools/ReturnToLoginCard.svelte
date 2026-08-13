<script lang="ts">
  import { goto } from "$app/navigation";
  import { LogOut } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button";
  import { Card, CardContent, CardHeader, CardTitle } from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
</script>

<!-- Return to Login Card -->
<Card class="neushadow min-w-64 border-0">
  <CardHeader>
    <CardTitle class="flex items-center gap-2">
      <LogOut class="h-5 w-5" />
      {t.DevTool.ReturnToLogin()}
    </CardTitle>
  </CardHeader>
  <CardContent>
    <Button onclick={() => goto("/auth")} variant="outline" class="w-full">
      <LogOut class="mr-2 h-4 w-4" />
      {t.DevTool.ReturnToLogin()}
    </Button>
  </CardContent>
</Card>

<script lang="ts">
	import { Button } from '$lib/components/ui/button/index.js';
	import { Label } from '$lib/components/ui/label/index.js';
	import { Input } from '$lib/components/ui/input/index.js';
	import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '$lib/components/ui/card/index.js';
	import { Help } from '$lib/components/ui/help/index.js';

	let showTip = $state(false);
</script>

<Card class="w-full max-w-sm mx-auto mt-6 gap-4">
  <CardHeader>
    <CardTitle class="mx-auto text-xl">
      Login to Alasio
    </CardTitle>
  </CardHeader>
  <CardContent>
    <form>
      <div class="flex flex-col">
        <div class="grid gap-2">
          <div class="flex items-center">
            <Label for="password" class="text-sm">Password</Label>
            <Button
              variant="link"
              class="ml-auto text-xs text-muted-foreground p-0 h-auto"
              onclick={() => showTip = !showTip}
            >
              Forgot password?
            </Button>
          </div>
          <Input id="password" type="password" required />
          {#if showTip}
            <Help>
              If you forgot your password, you can always peep in file "/config/deploy.yaml" key "Password"
            </Help>
          {/if}
        </div>
      </div>
    </form>
  </CardContent>
  <CardFooter class="flex-col gap-2">
    <Button type="submit" class="w-full">Login</Button>
  </CardFooter>
</Card>
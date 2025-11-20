<script lang="ts">
  import { goto } from "$app/navigation";
  import { authApi, type jwtError } from "$lib/api/auth";
  import { Button } from "$lib/components/ui/button/index.js";
  import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "$lib/components/ui/card/index.js";
  import { Help } from "$lib/components/ui/help/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import { Label } from "$lib/components/ui/label/index.js";
  import { t } from "$lib/i18n";

  let showTip = $state(false);
  let error = $state<jwtError | null>(null);
  let password = $state("");

  async function login() {
    // close tip
    showTip = false;
    try {
      let response = await authApi.login.call(password);

      // Success cases: 200 or 204 redirect to home
      if (response.is(200) || response.is(204)) {
        goto("/");
        return;
      }

      // Error cases: 429, 403, 500 show error messages
      if (response.is(401) || response.is(403) || response.is(429)) {
        error = response.data;
      }
    } catch (err) {
      // Handle network errors or other exceptions
      error = {
        message: t.Auth.NetworkError(),
        remain: 0,
        after: 600,
      };
    }
  }

  // Handle form submission
  function handleSubmit(event: Event) {
    event.preventDefault();
    login();
  }
</script>

<Card class="neushadow mx-auto mt-6 w-full max-w-sm gap-4 border-none">
  <CardHeader>
    <CardTitle class="mx-auto text-xl">{t.Auth.LoginToAlasio()}</CardTitle>
  </CardHeader>
  <CardContent>
    <form onsubmit={handleSubmit}>
      <div class="flex flex-col">
        <div class="grid gap-2">
          <div class="flex items-center">
            <Label for="password" class="text-sm">{t.Auth.Password()}</Label>
            <Button
              variant="link"
              class="text-muted-foreground ml-auto h-auto p-0 text-xs"
              onclick={() => (showTip = !showTip)}
            >
              {t.Auth.ForgotPassword()}
            </Button>
          </div>
          <Input id="password" type="password" bind:value={password} />
          {#if showTip}
            <Help>{t.Auth.PasswordTip()}</Help>
          {/if}
          {#if error}
            <Help variant="error">
              {#if error.message === "failure"}
                {t.Auth.LoginFailed()}<br />
                {t.Auth.RemainingAttempts()}: {error.remain}
              {:else if error.message === "banned"}
                {t.Auth.IpBanned()}<br />
                {t.Auth.TryAgainIn({ minutes: Math.ceil(error.after / 60) })}
              {:else}
                {error.message}
              {/if}
            </Help>
          {/if}
        </div>
      </div>
    </form>
  </CardContent>
  <CardFooter class="flex-col gap-2">
    <Button type="submit" class="w-full" onclick={login}>{t.Auth.Login()}</Button>
  </CardFooter>
</Card>

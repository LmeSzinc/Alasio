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
  // Disable the login button until a password is entered
  let canLogin = $derived(password.length > 0);
  // Plaintext warning for remote http sessions (lan mode): the password
  // and the JWT cookie travel in clear text on the network. Loopback is
  // trustworthy, remote http is only suggested on trusted networks.
  let insecure = $state(false);
  {
    const hostname = window.location.hostname;
    const loopback = hostname === "127.0.0.1" || hostname === "::1" || hostname === "localhost";
    insecure = window.location.protocol === "http:" && !loopback;
  }

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
      error = { err: "NETWORK_ERROR" };
    }
  }

  // Translate the backend error key to a localized message
  function errorText(err: jwtError): string {
    switch (err.err) {
      case "FAIL2BAN_LOGIN_FAILED":
        return t.Auth.ErrLoginFailed({ remain: err.data?.remain ?? 0 });
      case "FAIL2BAN_BANNED":
        return t.Auth.ErrIpBanned({ minutes: Math.ceil((err.data?.after ?? 0) / 60) });
      case "FAIL2BAN_TOO_MANY_REQUEST":
        return t.Auth.ErrTooManyRequests({ seconds: err.data?.after ?? 0 });
      case "AUTH_TOKEN_INVALID":
        return t.Auth.ErrTokenInvalid();
      case "AUTH_ELECTRON_ONLY":
        return t.Auth.ErrElectronOnly();
      case "DEPLOY_PASSWORD_NOT_SET":
        return t.Auth.ErrDeployPasswordNotSet();
      case "DEPLOY_PASSWORD_TOO_WEAK":
        // the frontend keeps its own translation. The text mirrors the
        // backend reference WEAK_PASSWORD_MESSAGE
        // (alasio/backend/auth/password.py): the two are maintained
        // independently and kept in sync by hand (manual contract)
        return t.Auth.ErrDeployPasswordTooWeak();
      case "DEPLOY_CERT_NOT_SET":
        return t.Auth.ErrDeployCertNotSet();
      case "NETWORK_ERROR":
        return t.Auth.NetworkError();
      default:
        return err.err;
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
          {#if insecure}
            <Help>{t.Auth.PlaintextWarning()}</Help>
          {/if}
          {#if showTip}
            <Help>{t.Auth.PasswordTip()}</Help>
          {/if}
          {#if error}
            <Help variant="error">{errorText(error)}</Help>
          {/if}
        </div>
      </div>
    </form>
  </CardContent>
  <CardFooter class="flex-col gap-2">
    <Button type="submit" class="w-full" disabled={!canLogin} onclick={login}>{t.Auth.Login()}</Button>
  </CardFooter>
</Card>

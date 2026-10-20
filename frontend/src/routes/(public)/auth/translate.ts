import type { jwtError } from "$lib/api/auth";
import { t } from "$lib/i18n";

/**
 * Translate an auth error key returned by the backend into a localized
 * message.
 *
 * Args:
 *     err (jwtError): Error body returned by the auth endpoints
 *
 * Returns:
 *     str: Localized message, or the raw key when it has no translation
 */
export function errorText(err: jwtError): string {
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

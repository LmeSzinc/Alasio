import { redirect } from "@sveltejs/kit";
import type { LayoutLoad } from "./$types";
import { authApi } from "$lib/api/auth";

export const load: LayoutLoad = async ({ fetch, url }) => {
  // User must have a valid token, otherwise redirect to login page
  const response = await authApi.renew.call();
  if (response.is(401) || response.is(403)) {
    throw redirect(307, `/auth`);
  }
};

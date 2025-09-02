import { redirect } from "@sveltejs/kit";
import type { LayoutLoad } from "./$types";
import { authApi } from "$lib/api/auth";

export const load: LayoutLoad = async () => {
  try {
    // User must have a valid token, otherwise redirect to login page
    const response = await authApi.renew.call();
    if (response.is(401) || response.is(403)) {
      throw redirect(307, `/auth`);
    }

    if (response.status === 200) {
      // success
      return { success: true, data: null };
    } else {
      // any captured error
      console.error(response.data);
      return { success: false, errorMsg: response.data?.message };
    }
  } catch (error: any) {
    // redirect
    if (error.status && error.status >= 300 && error.status < 400) {
      throw error;
    }
    // any uncaptured error
    console.error("Unexpected internal error during auth:", error);
    return { success: false, errorMsg: "Unexpected internal error during auth" };
  }
};

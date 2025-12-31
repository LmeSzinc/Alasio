import { redirect } from "@sveltejs/kit";
import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ params }) => {
  throw redirect(302, `/config/${params.config_name}/overview`);
};

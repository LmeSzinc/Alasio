import type { LayoutLoad } from "./$types";

export const load: LayoutLoad = ({ params }) => {
  // Get config_name from URL
  const rawConfigName = params.config_name;
  const decodedConfigName = decodeURIComponent(rawConfigName);
  return {
    config_name: decodedConfigName,
  };
};

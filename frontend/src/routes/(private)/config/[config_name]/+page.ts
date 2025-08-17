import type { PageLoad } from "./$types";

export const load: PageLoad = ({ params }) => {
  // Get config_name from URL
  const rawConfigName = params.config_name;
  const decodedConfigName = decodeURIComponent(rawConfigName);
  return {
    config_name: decodedConfigName,
  };
};

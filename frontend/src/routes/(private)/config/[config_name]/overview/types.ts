export interface LogDataProps {
  // timestamp in seconds, in UTC
  t: number;
  // logging level in uppercase:
  //   DEBUG, INFO, WARNING, ERROR, CRITICAL, and maybe custom level
  l: string;
  // log message, might contain "\n"
  m: string;
  // exception track back if error occurs, might contain "\n"
  e?: string;
}

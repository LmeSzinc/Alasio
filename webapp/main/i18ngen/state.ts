// Auto-generated language state (node mode)
import { DEFAULT_LANG } from "./constants";

let currentLang: string = DEFAULT_LANG;

export function setLang(lang: string) {
  currentLang = lang;
}

export function getLang(): string {
  return currentLang;
}

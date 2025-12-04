// Language constants
export const L_zh_CN = 'zh-CN';
export const L_en_US = 'en-US';
export const L_ja_JP = 'ja-JP';
export const L_zh_TW = 'zh-TW';
export const L_es_ES = 'es-ES';

// Global i18n state
export const i18nState = $state({ l: L_en_US });

export function setLanguage(lang: string) {
  i18nState.l = lang;
  window.electronAPI?.setLanguage(lang);
  window.electronAPI?.updateTrayLanguage(lang);
}

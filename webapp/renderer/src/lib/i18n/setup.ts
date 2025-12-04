import { i18nState, L_zh_CN, L_en_US, L_ja_JP, L_zh_TW, L_es_ES } from './index.svelte';

export const Welcome = () => {
  if (i18nState.l === L_zh_CN) return `欢迎使用`;
  if (i18nState.l === L_ja_JP) return `ようこそ`;
  if (i18nState.l === L_zh_TW) return `歡迎使用`;
  if (i18nState.l === L_es_ES) return `Bienvenido`;
  return `Welcome`;
};

export const SelectLanguage = () => {
  if (i18nState.l === L_zh_CN) return `选择语言`;
  if (i18nState.l === L_ja_JP) return `言語を選択`;
  if (i18nState.l === L_zh_TW) return `選擇語言`;
  if (i18nState.l === L_es_ES) return `Seleccionar idioma`;
  return `Select Language`;
};

export const Start = () => {
  if (i18nState.l === L_zh_CN) return `开始使用`;
  if (i18nState.l === L_ja_JP) return `スタート`;
  if (i18nState.l === L_zh_TW) return `開始使用`;
  if (i18nState.l === L_es_ES) return `Iniciar`;
  return `Start`;
};

import { i18nState, L_zh_CN, L_en_US, L_ja_JP, L_zh_TW, L_es_ES } from './index.svelte';

export const ConfigNotFound = () => {
  if (i18nState.l === L_zh_CN) return `未找到配置文件`;
  if (i18nState.l === L_ja_JP) return `設定ファイルが見つかりません`;
  if (i18nState.l === L_zh_TW) return `未找到配置文件`;
  if (i18nState.l === L_es_ES) return `Archivo de configuración no encontrado`;
  return `Configuration file not found`;
};

export const PythonNotFound = () => {
  if (i18nState.l === L_zh_CN) return `Python 可执行文件未找到`;
  if (i18nState.l === L_ja_JP) return `Python実行ファイルが見つかりません`;
  if (i18nState.l === L_zh_TW) return `Python 可執行文件未找到`;
  if (i18nState.l === L_es_ES) return `Ejecutable de Python no encontrado`;
  return `Python executable not found`;
};

export const GuiPyNotFound = () => {
  if (i18nState.l === L_zh_CN) return `gui.py 未找到`;
  if (i18nState.l === L_ja_JP) return `gui.py が見つかりません`;
  if (i18nState.l === L_zh_TW) return `gui.py 未找到`;
  if (i18nState.l === L_es_ES) return `gui.py no encontrado`;
  return `gui.py not found`;
};

export const CurrentPath = () => {
  if (i18nState.l === L_zh_CN) return `当前路径`;
  if (i18nState.l === L_ja_JP) return `現在のパス`;
  if (i18nState.l === L_zh_TW) return `當前路徑`;
  if (i18nState.l === L_es_ES) return `Ruta actual`;
  return `Current path`;
};

import { i18nState, L_zh_CN, L_en_US, L_ja_JP, L_zh_TW, L_es_ES } from './index.svelte';

export const Title = () => {
  if (i18nState.l === L_zh_CN) return `确认关闭`;
  if (i18nState.l === L_ja_JP) return `閉じる確認`;
  if (i18nState.l === L_zh_TW) return `確認關閉`;
  if (i18nState.l === L_es_ES) return `Confirmar Cierre`;
  return `Confirm Close`;
};

export const Message = () => {
  if (i18nState.l === L_zh_CN) return `确定要关闭 Alasio 吗？`;
  if (i18nState.l === L_ja_JP) return `Alasio を終了してもよろしいですか？`;
  if (i18nState.l === L_zh_TW) return `確定要關閉 Alasio 嗎？`;
  if (i18nState.l === L_es_ES) return `¿Estás seguro de que quieres cerrar Alasio?`;
  return `Are you sure you want to close Alasio?`;
};

export const Cancel = () => {
  if (i18nState.l === L_zh_CN) return `取消`;
  if (i18nState.l === L_ja_JP) return `キャンセル`;
  if (i18nState.l === L_zh_TW) return `取消`;
  if (i18nState.l === L_es_ES) return `Cancelar`;
  return `Cancel`;
};

export const Confirm = () => {
  if (i18nState.l === L_zh_CN) return `确认`;
  if (i18nState.l === L_ja_JP) return `確認`;
  if (i18nState.l === L_zh_TW) return `確認`;
  if (i18nState.l === L_es_ES) return `Confirmar`;
  return `Confirm`;
};

export const Closing = () => {
  if (i18nState.l === L_zh_CN) return `正在关闭...`;
  if (i18nState.l === L_ja_JP) return `終了中...`;
  if (i18nState.l === L_zh_TW) return `正在關閉...`;
  if (i18nState.l === L_es_ES) return `Cerrando...`;
  return `Closing...`;
};

export const WaitingBackend = () => {
  if (i18nState.l === L_zh_CN) return `正在等待后端关闭...`;
  if (i18nState.l === L_ja_JP) return `バックエンドの終了を待っています...`;
  if (i18nState.l === L_zh_TW) return `正在等待後端關閉...`;
  if (i18nState.l === L_es_ES) return `Esperando que el backend se cierre...`;
  return `Waiting for backend to close...`;
};

export const ForcingBackend = () => {
  if (i18nState.l === L_zh_CN) return `正在强制关闭后端...`;
  if (i18nState.l === L_ja_JP) return `バックエンドを強制終了しています...`;
  if (i18nState.l === L_zh_TW) return `正在強制關閉後端...`;
  if (i18nState.l === L_es_ES) return `Forzando cierre del backend...`;
  return `Forcing backend shutdown...`;
};

export const KillingBackend = () => {
  if (i18nState.l === L_zh_CN) return `正在终止后端进程...`;
  if (i18nState.l === L_ja_JP) return `バックエンドプロセスを終了しています...`;
  if (i18nState.l === L_zh_TW) return `正在終止後端進程...`;
  if (i18nState.l === L_es_ES) return `Terminando proceso del backend...`;
  return `Terminating backend process...`;
};
